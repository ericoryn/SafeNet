import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { runtime, invalidateRuntime } from "../runtime.mjs";

const disclaimer =
  "Система выявляет признаки для ручной проверки. Окончательное решение принимает аналитик.";
export default async function handler(req, res) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  try {
    const incoming = new URL(req.url, "https://safenet.local");
    const parameter = req.query?.path;
    const path =
      (Array.isArray(parameter) ? parameter.join("/") : parameter) ??
      incoming.searchParams.get("path") ??
      (incoming.pathname.startsWith("/backend/")
        ? incoming.pathname.slice(9)
        : "");
    if (
      !/^(health|ingestions|jobs|cases|summary|exports|docs|openapi\.json)(\/[^?#]*)?$/.test(
        path,
      )
    ) {
      res.statusCode = 404;
      return res.end(JSON.stringify({ error_code: "not_found", disclaimer }));
    }
    incoming.searchParams.delete("path");
    const chunks = [];
    let size = 0;
    if (!["GET", "HEAD"].includes(req.method)) {
      if (req.body !== undefined) {
        chunks.push(
          Buffer.isBuffer(req.body)
            ? req.body
            : Buffer.from(
                typeof req.body === "string"
                  ? req.body
                  : JSON.stringify(req.body),
              ),
        );
      } else {
        for await (const chunk of req) {
          size += chunk.length;
          if (size > 4_000_000) break;
          chunks.push(chunk);
        }
      }
      size = Math.max(
        size,
        chunks.reduce((total, chunk) => total + chunk.length, 0),
      );
      if (size > 4_000_000) {
        res.statusCode = 413;
        return res.end(
          JSON.stringify({ error_code: "input_too_large", disclaimer }),
        );
      }
    }
    const server = await runtime();
    const url = new URL("/" + path, server.origin);
    url.search = incoming.search;
    const response = await fetch(url, {
      method: req.method,
      headers: {
        "X-SafeNet-Access": server.key,
        ...(req.headers["content-type"]
          ? { "Content-Type": req.headers["content-type"] }
          : {}),
      },
      body: chunks.length ? Buffer.concat(chunks) : undefined,
      redirect: "manual",
      signal: AbortSignal.timeout(250000),
    });
    if (response.status === 502 || response.status === 503) invalidateRuntime();
    res.statusCode = response.status;
    for (const key of [
      "content-type",
      "content-disposition",
      "cache-control",
      "retry-after",
    ]) {
      if (response.headers.has(key))
        res.setHeader(key, response.headers.get(key));
    }
    res.setHeader("Cache-Control", "no-store");
    if (response.body) await pipeline(Readable.fromWeb(response.body), res);
    else res.end();
  } catch {
    invalidateRuntime();
    if (!res.headersSent) {
      res.statusCode = 503;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Retry-After", "2");
      res.end(
        JSON.stringify({ error_code: "backend_unavailable", disclaimer }),
      );
    } else res.end();
  }
}
