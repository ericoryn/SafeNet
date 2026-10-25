let locale = "ru";
let kazakh = {};
export function setMessages(messages) { kazakh = messages; }
export function getLocale() { return locale; }
export function dateLocale() { return locale === "kk" ? "kk-KZ" : "ru-RU"; }
export function setLocale(value) {
  if (!["ru", "kk"].includes(value)) throw Error("Unsupported locale");
  locale = value;
  try { localStorage.setItem("safenet-language", value); } catch { /* Private browsing may deny storage. */ }
}
export function t(source, values = {}) {
  const key = String(source).replace(/\s+/g, " ").trim();
  const translated = locale === "kk" ? (kazakh[key] ?? source) : source;
  return String(translated).replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match);
}
export async function initI18n() {
  const response = await fetch("/locales/kk.json");
  if (!response.ok) throw Error("Не удалось загрузить перевод интерфейса.");
  setMessages(await response.json());
  let saved;
  try { saved = localStorage.getItem("safenet-language"); } catch { saved = null; }
  setLocale(saved === "kk" ? "kk" : "ru");
}
export function localizeStatic() {
  document.documentElement.lang = locale;
  for (const element of document.querySelectorAll("[data-i18n]"))
    element.textContent = t(element.dataset.i18n);
  for (const attribute of ["aria-label", "title", "placeholder"]) {
    for (const element of document.querySelectorAll(`[data-i18n-${attribute}]`))
      element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`)));
  }
  for (const button of document.querySelectorAll("[data-locale]"))
    button.setAttribute("aria-pressed", String(button.dataset.locale === locale));
}
