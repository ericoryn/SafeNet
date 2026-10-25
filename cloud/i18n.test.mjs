import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { t, setLocale, setMessages } from "./assets/i18n.js";
const catalog = JSON.parse(readFileSync(new URL("./assets/locales/kk.json", import.meta.url), "utf8"));

test("казахские переводы покрывают интерфейс, ошибки и описания признаков", () => {
  const js = readFileSync(new URL("./assets/app.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("./assets/index.html", import.meta.url), "utf8");
  const features = readFileSync(new URL("../src/safenet/features/catalog.py", import.meta.url), "utf8");
  const keys = [...js.matchAll(/\bt\("((?:[^"\\]|\\.)*)"/g)].map(m => JSON.parse('"' + m[1] + '"'));
  for (const m of html.matchAll(/data-i18n(?:-aria-label|-title|-placeholder)?="([^"]+)"/g)) keys.push(m[1].replaceAll("&quot;", '"').replaceAll("&amp;", "&"));
  for (const source of [js.slice(js.indexOf("const names"), js.indexOf("function label")), features]) {
    for (const m of source.matchAll(/"([^"\n]*[А-Яа-я][^"\n]*)"/g)) keys.push(m[1]);
  }
  const missing = keys.map(k => k.replace(/\s+/g, " ").trim()).filter(k => /[А-Яа-я]/.test(k) && !Object.hasOwn(catalog, k));
  assert.deepEqual([...new Set(missing)], []);
  for (const [key, translated] of Object.entries(catalog)) {
    assert(translated.trim());
    assert.deepEqual([...key.matchAll(/\{\w+\}/g)].map(m => m[0]).sort(), [...translated.matchAll(/\{\w+\}/g)].map(m => m[0]).sort(), key);
  }
});
test("переключение, параметры, закрытое хранилище и неизменный исходный текст", () => {
  setMessages(catalog);
  setLocale("kk");
  assert.equal(t("Материалы"), "Материалдар");
  assert.equal(t("Открыть материал {id}", {id: "abc"}), "abc материалын ашу");
  assert.equal(t("Неизвестный исходный текст"), "Неизвестный исходный текст");
  assert.throws(() => setLocale("en"));
  setLocale("ru");
  assert.equal(t("Материалы"), "Материалы");
});
