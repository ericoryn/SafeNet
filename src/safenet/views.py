"""Bounded SQL views; queue summaries never load evidence JSON or image blobs."""

import base64
import json
import math
from datetime import UTC, datetime
from uuid import UUID

from safenet import DISCLAIMER
from safenet.contracts import (
    AnalysisResult,
    GraphEdge,
    GraphNeighborhood,
    GraphNode,
    GraphSnapshot,
    PreparedContent,
)
from safenet.graph import node_id
from safenet.http_contracts import fingerprint
from safenet.service import ServiceError
from safenet.storage import connect_database

CASE_SELECT = """SELECT c.case_id,c.provenance,c.synthetic,c.language,c.source_kind,c.created_at,
    j.status,j.error_code,q.queue_score,q.queue_level,q.latest_result_id AS result_id,
    r.version,q.last_action FROM case_index q JOIN cases c USING(case_id)
    JOIN jobs j ON j.job_id=q.latest_job_id
    LEFT JOIN analysis_results r ON r.result_id=q.latest_result_id """


def encode_cursor(value) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(value: str):
    try:
        if len(value) > 1024:
            raise ValueError()
        return json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
    except (ValueError, TypeError):
        raise ServiceError(422, "invalid_cursor") from None


def job_views(connection, ids: list[str]) -> list[dict]:
    rows = connection.execute(
        "SELECT j.*,o.version,r.result_id FROM json_each(?) requested "
        "JOIN jobs j ON j.job_id=requested.value JOIN job_options o USING(job_id) "
        "LEFT JOIN analysis_results r USING(job_id) ORDER BY CAST(requested.key AS INTEGER)",
        (json.dumps(ids),),
    )
    return [{key: row[key] for key in row.keys() if key != "payload_fingerprint"} for row in rows]


def list_cases(
    connection,
    *,
    limit=50,
    cursor=None,
    track=None,
    level=None,
    language=None,
    status=None,
    action=None,
    since=None,
    until=None,
) -> dict:
    clauses, params = [], []
    query = CASE_SELECT.replace(
        "SELECT c.case_id,", "SELECT substr(c.sanitized_text,1,160) AS preview,c.case_id,"
    )
    if track is not None:
        query += " JOIN track_scores t ON t.result_id=q.latest_result_id AND t.track=? "
        params.append(track.value)
    for value, column in (
        (level, "t.level" if track else "q.queue_level"),
        (language, "c.language"),
        (status, "j.status"),
        (action, "q.last_action"),
    ):
        if value is not None:
            clauses.append(column + "=?")
            params.append(value.value)
    for value, op in ((since, ">="), (until, "<=")):
        if value is not None:
            clauses.append("c.created_at" + op + "?")
            params.append(value.astimezone(UTC).isoformat())
    filters = fingerprint(params)
    if cursor is not None:
        try:
            data = decode_cursor(cursor)
            score, identity = data["score"], str(UUID(data["case_id"]))
            if (
                isinstance(score, bool)
                or not isinstance(score, (float, int))
                or not math.isfinite(score)
                or not -1 <= score <= 100
                or data["filter"] != filters
            ):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ServiceError(422, "invalid_cursor") from None
        clauses.append(
            "(coalesce(q.queue_score,-1)<? OR (coalesce(q.queue_score,-1)=? AND q.case_id>?))"
        )
        params += [score, score, identity]
    query += (" WHERE " + " AND ".join(clauses)) if clauses else ""
    query += " ORDER BY coalesce(q.queue_score,-1) DESC,q.case_id LIMIT ?"
    rows = [dict(row) for row in connection.execute(query, (*params, limit + 1))]
    more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if more:
        last = items[-1]
        next_cursor = encode_cursor(
            {
                "score": last["queue_score"] if last["queue_score"] is not None else -1,
                "case_id": last["case_id"],
                "filter": filters,
            }
        )
    return {"items": items, "next_cursor": next_cursor, "disclaimer": DISCLAIMER}


def case_detail(connection, case_id: str) -> dict:
    row = connection.execute(CASE_SELECT + " WHERE q.case_id=?", (case_id,)).fetchone()
    if row is None:
        raise ServiceError(404, "not_found")
    prepared = connection.execute(
        "SELECT prepared_json FROM case_preparations WHERE case_id=?", (case_id,)
    ).fetchone()
    result = connection.execute(
        "SELECT result_json FROM analysis_results WHERE result_id=?", (row["result_id"],)
    ).fetchone()
    return {
        "case": dict(row),
        "prepared": PreparedContent.model_validate_json(prepared[0]).model_dump(
            mode="json", exclude={"variants"}
        )
        if prepared
        else None,
        "latest_result": json.loads(result[0]) if result else None,
        "disclaimer": DISCLAIMER,
    }


def decisions(connection, case_id: str, limit: int, cursor: str | None) -> dict:
    if not connection.execute("SELECT 1 FROM cases WHERE case_id=?", (case_id,)).fetchone():
        raise ServiceError(404, "not_found")
    query, params = "SELECT * FROM analyst_decisions WHERE case_id=?", [case_id]
    if cursor is not None:
        try:
            stamp, identity = decode_cursor(cursor)
            stamp = datetime.fromisoformat(stamp).isoformat()
            identity = str(UUID(identity))
        except (ValueError, TypeError):
            raise ServiceError(422, "invalid_cursor") from None
        query += " AND (created_at>? OR (created_at=? AND decision_id>?))"
        params += [stamp, stamp, identity]
    query += " ORDER BY created_at,decision_id LIMIT ?"
    rows = [dict(row) for row in connection.execute(query, (*params, limit + 1))]
    items = rows[:limit]
    next_cursor = (
        encode_cursor([items[-1]["created_at"], items[-1]["decision_id"]])
        if len(rows) > limit
        else None
    )
    return {"items": items, "next_cursor": next_cursor, "disclaimer": DISCLAIMER}


def graph_view(
    connection, case_id: str, result_id: str | None, max_nodes: int, max_edges: int
) -> GraphNeighborhood:
    result = connection.execute(
        "SELECT r.result_json,o.context_id FROM analysis_results r "
        "JOIN job_options o USING(job_id) WHERE r.case_id=? "
        + ("AND r.result_id=?" if result_id else "ORDER BY r.version DESC LIMIT 1"),
        (case_id, result_id) if result_id else (case_id,),
    ).fetchone()
    if result is None:
        raise ServiceError(404, "not_found")
    analysis = AnalysisResult.model_validate_json(result[0])
    if analysis.graph_snapshot is None:
        raise ServiceError(409, "graph_unavailable")
    context_id = result[1]
    event = connection.execute(
        "SELECT sequence FROM graph_events WHERE context_id=? AND case_id=?", (context_id, case_id)
    ).fetchone()
    if event is None:
        raise ServiceError(409, "graph_unavailable")
    sequence = max(event[0], analysis.graph_snapshot.sequence)
    snapshot = connection.execute(
        "SELECT snapshot_json FROM graph_snapshots WHERE context_id=? AND sequence=?",
        (context_id, sequence),
    ).fetchone()
    join = (
        "FROM graph_edges f JOIN graph_edges e ON e.context_id=f.context_id AND e.target=f.target "
        "WHERE f.context_id=? AND f.case_id=? AND e.sequence<=? AND f.sequence<=?"
    )
    params = (context_id, case_id, sequence, sequence)
    total = connection.execute("SELECT count(*) " + join, params).fetchone()[0]
    root = node_id("case", case_id)
    ids, edges = {root}, []
    roots = connection.execute(
        "SELECT edge_json FROM graph_edges WHERE context_id=? AND case_id=? "
        "AND sequence<=? ORDER BY target LIMIT ?",
        (context_id, case_id, sequence, min(max_nodes - 1, max_edges)),
    ).fetchall()
    targets = []
    for row in roots:
        edge = GraphEdge.model_validate_json(row[0])
        targets.append(edge.target)
        ids.add(edge.target)
        edges.append(edge)
    candidates = connection.execute(
        "SELECT edge_json FROM graph_edges WHERE context_id=? "
        "AND target IN (SELECT value FROM json_each(?)) AND sequence<=? AND case_id<>? "
        "ORDER BY target,sequence,case_id LIMIT ?",
        (context_id, json.dumps(targets), sequence, case_id, max_edges - len(edges)),
    )
    for row in candidates:
        edge = GraphEdge.model_validate_json(row[0])
        new = {edge.source, edge.target} - ids
        if len(ids) + len(new) > max_nodes:
            break
        ids.update(new)
        edges.append(edge)
    nodes = [
        GraphNode.model_validate_json(row[0])
        for row in connection.execute(
            "SELECT n.node_json FROM graph_nodes n JOIN json_each(?) ids ON n.node_id=ids.value "
            "WHERE n.context_id=? ORDER BY n.node_id",
            (json.dumps(sorted(ids)), context_id),
        )
    ]
    return GraphNeighborhood(
        snapshot=GraphSnapshot.model_validate_json(snapshot[0]),
        focus_case_id=UUID(case_id),
        nodes=tuple(nodes),
        edges=tuple(edges),
        total_edges=total,
        truncated=len(edges) < total,
    )


def export_rows(database):
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        counts = {}
        yield (
            json.dumps({"record_type": "begin", "export_version": 1, "disclaimer": DISCLAIMER})
            + "\n"
        )
        queries = [
            (
                "case",
                CASE_SELECT.replace("SELECT c.case_id,", "SELECT c.sanitized_text,c.case_id,"),
            ),
            (
                "result",
                "SELECT result_json AS payload FROM analysis_results ORDER BY case_id,version",
            ),
            ("decision", "SELECT * FROM analyst_decisions ORDER BY created_at,decision_id"),
            (
                "job",
                "SELECT job_id,request_id,case_id,status,attempts,error_code,created_at,updated_at "
                "FROM jobs",
            ),
        ]
        for kind, query in queries:
            counts[kind] = 0
            for row in connection.execute(query):
                counts[kind] += 1
                item = json.loads(row[0]) if kind == "result" else dict(row)
                yield (
                    json.dumps(
                        {"record_type": kind, "data": item, "disclaimer": DISCLAIMER},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        yield (
            json.dumps(
                {"record_type": "end", "complete": True, "counts": counts, "disclaimer": DISCLAIMER}
            )
            + "\n"
        )
