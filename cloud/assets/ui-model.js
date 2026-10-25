const identity = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i;
export function readRoute(hash) {
  const [name, query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query),
    candidate = params.get("case");
  return {
    view: ["materials", "graph", "overview"].includes(name)
      ? name
      : "materials",
    caseId: candidate && identity.test(candidate) ? candidate : null,
  };
}
export function routeHash(view, caseId) {
  return "#" + view + (caseId ? "?case=" + encodeURIComponent(caseId) : "");
}
export function graphLayout(graph, focus, vertical = false) {
  const roots = graph.nodes.filter(
    (node) => node.kind === "case" && node.value === focus,
  );
  const entities = graph.nodes.filter((node) => node.kind !== "case");
  const peers = graph.nodes.filter(
    (node) => node.kind === "case" && node.value !== focus,
  );
  if (vertical) {
    const points = new Map();
    roots.forEach((item) => points.set(item.node_id, { x: 190, y: 95 }));
    entities.forEach((item, index) =>
      points.set(item.node_id, {
        x: entities.length === 1 ? 190 : 95 + (index % 2) * 190,
        y: 245 + Math.floor(index / 2) * 130,
      }),
    );
    const peerStart =
      245 + Math.ceil(entities.length / 2) * 130 + (entities.length ? 40 : 0);
    peers.forEach((item, index) =>
      points.set(item.node_id, {
        x: peers.length === 1 ? 190 : 95 + (index % 2) * 190,
        y: peerStart + Math.floor(index / 2) * 130,
      }),
    );
    return {
      points,
      width: 380,
      height: Math.max(520, peerStart + Math.ceil(peers.length / 2) * 130 + 50),
    };
  }
  const height = Math.max(
    440,
    Math.max(entities.length, peers.length) * 86 + 80,
  );
  const points = new Map();
  const column = (items, x) =>
    items.forEach((item, index) =>
      points.set(item.node_id, {
        x,
        y: (height * (index + 1)) / (items.length + 1),
      }),
    );
  column(roots, entities.length || peers.length ? 145 : 480);
  column(entities, peers.length ? 475 : 620);
  column(peers, 805);
  return { points, width: 960, height };
}
export function limitZoom(value) {
  return Math.min(2.5, Math.max(0.5, value));
}
export function graphPath(a, b, vertical) {
  if (vertical) {
    const down = a.y < b.y,
      start = a.y + (down ? 82 : -30),
      end = b.y + (down ? -30 : 82),
      middle = (start + end) / 2;
    return `M${a.x},${start} C${a.x},${middle} ${b.x},${middle} ${b.x},${end}`;
  }
  const right = a.x < b.x,
    start = a.x + (right ? 34 : -34),
    end = b.x + (right ? -30 : 30),
    middle = (start + end) / 2;
  return `M${start},${a.y} C${middle},${a.y} ${middle},${b.y} ${end},${b.y}`;
}
export function trackCounts(levels, track) {
  return Object.fromEntries(
    ["low", "medium", "high"].map((level) => [
      level,
      levels.find((item) => item.track === track && item.level === level)
        ?.cases || 0,
    ]),
  );
}
