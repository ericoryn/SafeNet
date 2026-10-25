"""Indexed bipartite graph. Only earlier base scores can influence a case."""

import hashlib
import json
import re
from bisect import bisect_right
from collections import Counter
from uuid import UUID

from safenet.config import DetectorConfig
from safenet.contracts import (
    AnalysisResult,
    GraphEdge,
    GraphInfluence,
    GraphNeighborhood,
    GraphNode,
    GraphScope,
    GraphSnapshot,
    PreparedContent,
    Track,
    TrackResult,
)
from safenet.scoring import risk_level


def node_id(kind: str, value: str) -> str:
    return kind + ":" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def template_key(prepared: PreparedContent) -> str | None:
    """Exact lexical skeleton; no evaluator family/campaign metadata is used."""
    text = prepared.variants[0].text
    text = re.sub(r"https?://\S+|\b(?:card|account)-[a-f0-9-]{36}\b", " ", text)
    # Remove the variable code strings, retaining the surrounding language.
    for entity in prepared.entities:
        if entity.kind == "promo_code":
            text = re.sub(r"(?<!\w)" + re.escape(entity.value) + r"(?!\w)", " ", text)
    words = re.findall(r"[^\W\d_]{2,}", text, flags=re.UNICODE)
    if len(words) < 8 or sum(map(len, words)) < 60:
        return None
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


def relationships(prepared: PreparedContent, sequence: int) -> dict[str, tuple[GraphNode, str]]:
    found = {}

    def add(kind, value, origin):
        identity = node_id(kind, value)
        found.setdefault(
            identity,
            (GraphNode(node_id=identity, kind=kind, value=value, first_seen=sequence), origin),
        )

    if prepared.content.synthetic and prepared.synthetic_account_id is not None:
        add("account", str(prepared.synthetic_account_id), "account_metadata")
    if prepared.content.synthetic and prepared.network is not None:
        network = prepared.network
        if network.group_id:
            origin = "group_metadata" if network.role == "member" else network.role + "_metadata"
            add("group", str(network.group_id), origin)
        for identity in network.channel_ids:
            add("channel", str(identity), "channel_metadata")
        for identity in network.audience_ids:
            add("audience", str(identity), "audience_metadata")
    for entity in prepared.entities:
        if entity.kind in ("domain", "promo_code"):
            add(entity.kind, entity.value, "text")
        elif prepared.content.synthetic and entity.kind in ("account", "synthetic_card"):
            value = str(UUID(entity.value.split("-", 1)[1]))
            add(entity.kind, value, "text")
    for domain in prepared.redirect_domains:
        add("domain", domain, "redirect_metadata")
    template = template_key(prepared)
    if template:
        add("template", template, "template")
    return dict(sorted(found.items()))


class GraphIndex:
    """One partition and config per index; processing order defines availability."""

    def __init__(self, config: DetectorConfig, scope: GraphScope):
        self.config = config
        identity = hashlib.sha256(f"graph-v1:{scope}:{config.fingerprint}".encode()).hexdigest()
        initial = GraphSnapshot(
            snapshot_id=identity,
            scope=scope,
            sequence=0,
            node_count=0,
            edge_count=0,
            component_count=0,
            largest_component_cases=0,
        )
        self.snapshots = [initial]
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[UUID, dict[str, GraphEdge]] = {}
        self.postings: dict[str, list[tuple[int, UUID]]] = {}
        self.seeds: dict[str, dict[Track, list[tuple[UUID, float]]]] = {}
        self.case_sequences: dict[UUID, int] = {}
        self.base_scores: dict[UUID, dict[Track, float]] = {}
        self._parent: dict[str, str] = {}
        self._size: dict[str, int] = {}
        self._case_count: dict[str, int] = {}
        self._components = 0
        self._largest = 0

    @property
    def snapshot(self) -> GraphSnapshot:
        return self.snapshots[-1]

    def _root(self, identity: str) -> str:
        while self._parent[identity] != identity:
            self._parent[identity] = self._parent[self._parent[identity]]
            identity = self._parent[identity]
        return identity

    def _add_node(self, node: GraphNode) -> None:
        if node.node_id in self.nodes:
            return
        self.nodes[node.node_id] = node
        self._parent[node.node_id] = node.node_id
        self._size[node.node_id] = 1
        self._case_count[node.node_id] = int(node.kind == "case")
        self._components += 1
        self._largest = max(self._largest, int(node.kind == "case"))

    def _union(self, left: str, right: str) -> None:
        left, right = self._root(left), self._root(right)
        if left == right:
            return
        if (self._size[left], left) < (self._size[right], right):
            left, right = right, left
        self._parent[right] = left
        self._size[left] += self._size[right]
        self._case_count[left] += self._case_count[right]
        self._components -= 1
        self._largest = max(self._largest, self._case_count[left])

    def process(
        self, prepared: PreparedContent, base: AnalysisResult, *, scope: GraphScope
    ) -> AnalysisResult:
        """Explain using the prior snapshot, then index only the unadjusted base."""
        if base.case_id in self.case_sequences:
            raise ValueError("case already indexed")
        result = self.preview(prepared, base, scope=scope)
        sequence = self.snapshot.sequence + 1
        links = relationships(prepared, sequence)
        case_node = GraphNode(
            node_id=node_id("case", str(base.case_id)),
            kind="case",
            value=str(base.case_id),
            first_seen=sequence,
        )
        self._add_node(case_node)
        edges = []
        for key, (node, origin) in links.items():
            self._add_node(node)
            self._union(case_node.node_id, key)
            self.postings.setdefault(key, []).append((sequence, base.case_id))
            seeds = self.seeds.setdefault(key, {track: [] for track in Track})
            for track in base.tracks:
                if track.score >= self.config.thresholds.high:
                    seeds[track.track].append((base.case_id, track.score))
            edges.append(
                GraphEdge(
                    source=case_node.node_id, target=key, origin=origin, observed_sequence=sequence
                )
            )
        self.edges[base.case_id] = {edge.target: edge for edge in edges}
        self.case_sequences[base.case_id] = sequence
        self.base_scores[base.case_id] = {track.track: track.score for track in base.tracks}
        payload = json.dumps(
            {
                "previous": self.snapshot.snapshot_id,
                "case": str(base.case_id),
                "keys": list(links),
                "scores": self.base_scores[base.case_id],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.snapshots.append(
            GraphSnapshot(
                snapshot_id=hashlib.sha256(payload.encode()).hexdigest(),
                scope=scope,
                sequence=sequence,
                node_count=len(self.nodes),
                edge_count=self.snapshot.edge_count + len(links),
                component_count=self._components,
                largest_component_cases=self._largest,
            )
        )
        return result

    def preview(
        self, prepared: PreparedContent, base: AnalysisResult, *, scope: GraphScope
    ) -> AnalysisResult:
        """Reanalysis uses the current snapshot with the case's own votes excluded."""
        if (
            scope != self.snapshot.scope
            or base.case_id != prepared.content.case_id
            or base.preprocessing_fingerprint != prepared.preprocessing_fingerprint
            or base.run.config_fingerprint != self.config.fingerprint
            or base.graph_snapshot is not None
            or any(track.graph_adjustment for track in base.tracks)
        ):
            raise ValueError("incompatible graph input, repeated case, or adjusted seed")
        sequence = self.snapshot.sequence + 1
        links = relationships(prepared, sequence)
        existing = base.case_id in self.case_sequences
        new_nodes = 1 + sum(key not in self.nodes for key in links)
        if not existing and (
            sequence > self.config.limits.max_records
            or len(self.nodes) + new_nodes > self.config.graph.max_nodes
            or self.snapshot.edge_count + len(links) > self.config.graph.max_edges
        ):
            raise ValueError("graph capacity exceeded")
        tracks = tuple(
            self._adjust(track, links, exclude=base.case_id if existing else None)
            for track in base.tracks
        )
        return AnalysisResult.model_validate(
            {**base.model_dump(), "tracks": tracks, "graph_snapshot": self.snapshot}
        )

    def _adjust(
        self, base: TrackResult, links: dict, *, exclude: UUID | None = None
    ) -> TrackResult:
        best, support = 0.0, None
        if base.score >= self.config.thresholds.medium and self.config.graph.max_adjustment:
            for key in links:
                seeds = self.seeds.get(key, {}).get(base.track, ())
                if not seeds:
                    continue
                own = exclude is not None and key in self.edges[exclude]
                own_seed = (
                    own and self.base_scores[exclude][base.track] >= self.config.thresholds.high
                )
                count = len(seeds) - int(own_seed)
                degree = len(self.postings[key]) - int(own)
                if not count or not degree:
                    continue
                seed = next(item for item in seeds if item[0] != exclude)
                adjustment = self.config.graph.max_adjustment * count / degree
                if adjustment > best:
                    best = adjustment
                    node = self.nodes[key]
                    support = GraphInfluence(
                        snapshot_id=self.snapshot.snapshot_id,
                        node_id=key,
                        kind=node.kind,
                        value=node.value,
                        previous_cases=degree,
                        high_base_cases=count,
                        seed_case_id=seed[0],
                        seed_base_score=seed[1],
                        seed_threshold=self.config.thresholds.high,
                        max_adjustment=self.config.graph.max_adjustment,
                    )
        total = sum(feature.contribution for feature in base.features) + best
        score = max(0.0, min(100.0, total))
        return TrackResult(
            track=base.track,
            features=base.features,
            graph_adjustment=best,
            graph_evidence=support,
            cap_adjustment=score - total,
            score=score,
            level=risk_level(score, self.config),
        )

    def neighborhood(
        self,
        case_id: UUID,
        *,
        sequence: int | None = None,
        max_nodes: int | None = None,
        max_edges: int | None = None,
    ) -> GraphNeighborhood:
        """Two-hop case/entity view, at the case's observation snapshot by default."""
        first = self.case_sequences[case_id]
        sequence = first if sequence is None else sequence
        if type(sequence) is not int or not first <= sequence <= self.snapshot.sequence:
            raise ValueError("case is not available in the requested snapshot")
        node_limit = self.config.graph.display_max_nodes if max_nodes is None else max_nodes
        edge_limit = self.config.graph.display_max_edges if max_edges is None else max_edges
        if (
            type(node_limit) is not int
            or type(edge_limit) is not int
            or not 1 <= node_limit <= self.config.graph.display_max_nodes
            or not 1 <= edge_limit <= self.config.graph.display_max_edges
        ):
            raise ValueError("invalid graph display bounds")
        root = node_id("case", str(case_id))
        nodes, edges = {root: self.nodes[root]}, []
        links = tuple(self.edges[case_id].values())
        counts = {
            edge.target: bisect_right(
                self.postings[edge.target], sequence, key=lambda pair: pair[0]
            )
            for edge in links
        }
        total = sum(counts.values())
        for link in links:
            if len(nodes) >= node_limit or len(edges) >= edge_limit:
                break
            nodes[link.target] = self.nodes[link.target]
            edges.append(link)
            for observed, peer in self.postings[link.target]:
                if observed > sequence or len(edges) >= edge_limit:
                    break
                if peer == case_id:
                    continue
                identity = node_id("case", str(peer))
                if identity not in nodes and len(nodes) >= node_limit:
                    break
                nodes[identity] = self.nodes[identity]
                edges.append(self.edges[peer][link.target])
        return GraphNeighborhood(
            snapshot=self.snapshots[sequence],
            focus_case_id=case_id,
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
            total_edges=total,
            truncated=len(edges) < total,
        )

    def export(self) -> dict:
        return {
            "graph_version": "1",
            "snapshot": self.snapshot.model_dump(mode="json"),
            "snapshots": [item.model_dump(mode="json") for item in self.snapshots],
            "nodes": [node.model_dump(mode="json") for node in self.nodes.values()],
            "edges": [
                edge.model_dump(mode="json")
                for edges in self.edges.values()
                for edge in edges.values()
            ],
            "top_components": self.top_components(),
        }

    def top_components(self) -> list[dict]:
        components = Counter(
            self._root(node_id("case", str(case_id))) for case_id in self.case_sequences
        )
        return [
            {"component_id": key, "cases": count}
            for key, count in sorted(components.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
        ]
