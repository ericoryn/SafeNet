import { test } from "node:test";
import assert from "node:assert/strict";
import {
  readRoute,
  routeHash,
  graphLayout,
  graphPath,
  limitZoom,
  trackCounts,
} from "./assets/ui-model.js";

test("навигация сохраняет материал и отклоняет произвольный путь", () => {
  const id = "7469314f-bc4d-4590-9f6b-916300e4b71a";
  assert.deepEqual(readRoute(routeHash("graph", id)), {
    view: "graph",
    caseId: id,
  });
  assert.deepEqual(readRoute("#graph?case=../../exports"), {
    view: "graph",
    caseId: null,
  });
  assert.deepEqual(readRoute("#unexpected"), {
    view: "materials",
    caseId: null,
  });
});
test("раскладка не теряет узлы и отделяет материал, сущности и соседей", () => {
  const nodes = [
    { node_id: "root", kind: "case", value: "focus" },
    ...Array.from({ length: 28 }, (_, i) => ({
      node_id: "d" + i,
      kind: "domain",
      value: "domain-" + i,
    })),
    { node_id: "peer", kind: "case", value: "peer" },
  ];
  const graph = { nodes };
  const layout = graphLayout(graph, "focus");
  assert.equal(layout.points.size, nodes.length);
  assert.equal(
    new Set([...layout.points.values()].map((p) => p.x + "," + p.y)).size,
    nodes.length,
  );
  assert(layout.points.get("root").x < layout.points.get("d0").x);
  assert(layout.points.get("d0").x < layout.points.get("peer").x);
  for (const p of layout.points.values())
    assert(p.y > 0 && p.y < layout.height && Number.isFinite(p.x));
});
test("пустой граф и масштаб имеют конечные границы", () => {
  assert.equal(graphLayout({ nodes: [] }, "missing").points.size, 0);
  assert.equal(limitZoom(-10), 0.5);
  assert.equal(limitZoom(100), 2.5);
  assert.equal(limitZoom(1.2), 1.2);
});

test("вертикальные связи обходят область подписи под узлом", () => {
  assert.equal(
    graphPath({ x: 190, y: 95 }, { x: 190, y: 245 }, true),
    "M190,177 C190,196 190,196 190,215",
  );
  assert.equal(
    graphPath({ x: 190, y: 415 }, { x: 190, y: 245 }, true),
    "M190,385 C190,356 190,356 190,327",
  );
});

test("на телефоне граф раскладывается вертикально без потери узлов", () => {
  const graph = {
    nodes: [
      { node_id: "a", kind: "case", value: "focus" },
      { node_id: "d", kind: "domain", value: "site.test" },
      { node_id: "b", kind: "case", value: "peer" },
    ],
  };
  const layout = graphLayout(graph, "focus", true);
  assert.equal(layout.width, 380);
  assert(layout.points.get("a").y < layout.points.get("d").y);
  assert(layout.points.get("d").y < layout.points.get("b").y);
  assert.equal(layout.points.size, 3);
});
test("сводка считает независимые треки и не выдумывает отсутствующие уровни", () => {
  assert.deepEqual(
    trackCounts(
      [
        { track: "casino", level: "high", cases: 2 },
        { track: "lottery", level: "high", cases: 9 },
      ],
      "casino",
    ),
    { low: 0, medium: 0, high: 2 },
  );
});
