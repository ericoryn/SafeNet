import { test } from "node:test";
import assert from "node:assert/strict";
import { Readable, Writable } from "node:stream";
import handler from "./api/gateway.mjs";

async function request(url, { body, chunks, query } = {}) {
  const req = Readable.from(chunks || []);
  Object.assign(req, {
    url,
    query,
    method: body === undefined && !chunks ? "GET" : "POST",
    headers: {},
    body,
  });
  let output = "";
  const headers = {};
  const res = new Writable({
    write(chunk, encoding, next) {
      output += chunk.toString();
      next();
    },
  });
  res.setHeader = (name, value) => {
    headers[name] = value;
  };
  await handler(req, res);
  return { status: res.statusCode, body: JSON.parse(output), headers };
}
test("запрещены внешние адреса и произвольные маршруты", async () => {
  for (const path of [
    "https://example.com",
    "//example.com",
    "../health",
    "admin",
  ]) {
    const response = await request("/backend/anything", { query: { path } });
    assert.equal(response.status, 404);
    assert.equal(response.body.error_code, "not_found");
  }
});
test("большое тело отклоняется до подключения к облаку", async () => {
  for (const options of [
    { body: Buffer.alloc(4000001) },
    { chunks: [Buffer.alloc(3000000), Buffer.alloc(1000001)] },
  ]) {
    const response = await request("/backend/ingestions", options);
    assert.equal(response.status, 413);
    assert.equal(response.body.error_code, "input_too_large");
    assert.match(response.headers["Content-Type"], /application\/json/);
  }
});
