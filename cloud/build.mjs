import { mkdir, readdir, readFile, writeFile, cp } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { marked } from "marked";

const output = "public";
await mkdir(output, { recursive: true });
await cp("assets", output, { recursive: true });
async function documents(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await documents(path);
    else if (/\.(json|jsonl|xml|toml|pdf|pptx|png)$/.test(entry.name)) {
      const destination = join(output, "docs", relative("docs", path));
      await mkdir(dirname(destination), { recursive: true });
      await cp(path, destination);
    }
    else if (entry.name.endsWith(".md")) {
      const name = relative("docs", path).replaceAll("\\", "/");
      const destination = join(output, "docs", name.replace(/\.md$/, ".html"));
      await mkdir(dirname(destination), { recursive: true });
      let html = marked.parse(await readFile(path, "utf8"));
      html = html.replace(/href="([^"]+)\.md(#[^"]*)?"/g, 'href="$1.html$2"');
      const title =
        /<h1>(.*?)<\/h1>/.exec(html)?.[1] || "Документация SafeNet KZ";
      await writeFile(
        destination,
        `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><link rel="stylesheet" href="/app.css"></head><body><header><a class="brand" href="/">SafeNet <span>KZ</span></a><nav><a href="/">Приложение</a><a href="/backend/docs">API</a></nav></header><article class="doc"><nav class="doc-nav"><a href="/docs/README.html">Обзор</a><a href="/docs/API.html">API</a><a href="/docs/DATA.html">Данные</a><a href="/docs/METHODOLOGY.html">Методика</a><a href="/docs/IMPLEMENTATION_PLAN.html">План</a><a href="/docs/DEPLOYMENT.html">Развёртывание</a></nav>${html}</article></body></html>`,
      );
    }
  }
}
await documents("docs");
console.log("Документация и двуязычный интерфейс собраны.");
