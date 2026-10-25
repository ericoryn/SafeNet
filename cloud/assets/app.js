import { t, initI18n, setLocale, localizeStatic, dateLocale } from "./i18n.js";
try { await initI18n(); } catch {
  document.querySelector('[data-locale="kk"]').disabled = true;
  document.querySelector('#message').textContent = 'Не удалось загрузить перевод. Доступна русская версия.';
}
localizeStatic();
import {
  readRoute,
  routeHash,
  graphLayout,
  graphPath,
  limitZoom,
  trackCounts,
} from "./ui-model.js";
const $ = (selector) => document.querySelector(selector);
const names = {
  lottery: "Лотереи",
  casino: "Казино",
  investment: "Инвестиции",
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
  queued: "В очереди",
  processing: "Обрабатывается",
  completed: "Обработан",
  incomplete: "Не завершён",
  failed: "Ошибка",
  ru: "Русский",
  kk: "Казахский",
  mixed: "Смешанный",
  unknown: "Не определён",
  text: "Текст",
  transcript: "Транскрипт",
  screenshot: "Скриншот",
  confirm_signs: "Подтвердить признаки",
  false_positive: "Ложное срабатывание",
  request_review: "Запросить проверку",
  domain: "Домен",
  account: "Аккаунт",
  synthetic_card: "Тестовая карта",
  promo_code: "Промокод",
  template: "Шаблон",
  case: "Материал",
  amount: "Сумма",
  payment_reference: "Маскированная карта",
  group: "Группа", channel: "Канал", audience: "Общая аудитория",
  organizer: "Организатор", curator: "Куратор", member: "Участник",
  organizer_metadata: "Организатор", curator_metadata: "Куратор",
  group_metadata: "Группа", channel_metadata: "Канал", audience_metadata: "Общая аудитория",
  account_metadata: "Аккаунт", redirect_metadata: "Цепочка переходов",
};
const errors = {
  backend_unavailable:
    "Сервер запускается или временно недоступен. Повторите запрос.",
  invalid_input: "Проверьте структуру входных данных.",
  input_too_large: "Превышен допустимый размер запроса.",
  source_required: "Для повтора нужен исходный материал. Загрузите его заново.",
  ocr_failed: "Не удалось распознать изображение.",
  interrupted: "Обработка прервана. Может потребоваться исходный материал.",
  queue_full: "Очередь заполнена. Повторите позже.",
  invalid_record: "Некорректная запись.",
};
function label(key) { return t(names[key] || key); }
let cursor = null,
  queueItems = [],
  summaryData = null,
  selected = null,
  detail = null,
  result = null,
  queueGeneration = 0,
  overviewGeneration = 0,
  detailGeneration = 0,
  routeGeneration = 0;
let activeView = "materials",
  graphCaseId = null,
  graphResultId = null,
  graphResultCaseId = null,
  graphGeneration = 0;
let graphFrame = { width: 960, height: 440 };
let graphRecords = new Map(),
  graphCursor = null,
  graphLoaded = false,
  graphModel = null,
  graphTimeline = [];
let graphZoom = 1,
  graphOffset = { x: 0, y: 0 };
function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
}
function message(text, error = false) {
  const banner = $("#message");
  banner.replaceChildren();
  banner.className = error ? "error" : "";
  banner.setAttribute("role", error ? "alert" : "status");
  if (!text) return;
  const close = node("button", "×", "icon-button");
  close.setAttribute("aria-label", t("Закрыть уведомление"));
  close.onclick = () => message("");
  banner.append(node("span", text), close);
}
function fail(error) {
  message(error.message || t("Запрос не выполнен."), true);
}
async function api(path, body) {
  const response = await fetch(
    "/backend" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  let value;
  try {
    value = await response.json();
  } catch {
    throw Error(t("Нет ответа API. Проверьте доступ к развёртыванию Vercel."));
  }
  if (!response.ok) {
    const error = Error(
      t(errors[value.error_code] || "") ||
        t("Ошибка API {status}: {code}", {status: response.status, code: value.error_code || t("запрос отклонён")}),
    );
    error.status = response.status;
    error.code = value.error_code;
    throw error;
  }
  return value;
}
function formatDate(value) {
  return new Date(value).toLocaleString(dateLocale());
}
async function overview() {
  const generation = ++overviewGeneration,
    feedback = $("#overview-feedback");
  feedback.textContent = t("Обновляем сводку…");
  feedback.hidden = false;
  feedback.setAttribute("role", "status");
  feedback.classList.remove("error");
  $("#overview-refresh").disabled = true;
  $(".summary-strip").setAttribute("aria-busy", "true");
  try {
    const data = await api("/summary");
    if (generation !== overviewGeneration) return;
    renderOverview(data);
    feedback.hidden = true;
  } catch (error) {
    if (generation !== overviewGeneration) return;
    const retry = node("button", t("Повторить загрузку"), "secondary");
    retry.onclick = () => {
      $("#overview-title").focus({ preventScroll: true });
      overview();
    };
    feedback.setAttribute("role", "alert");
    feedback.classList.add("error");
    feedback.replaceChildren(
      node("span", summaryData
        ? t("Не удалось обновить сводку. Показаны последние загруженные данные.")
        : t("Не удалось загрузить сводку.")),
      node("span", error.message),
      retry,
    );
  } finally {
    if (generation === overviewGeneration) {
      $("#overview-refresh").disabled = false;
      $(".summary-strip").setAttribute("aria-busy", "false");
    }
  }
}
function renderOverview(data) {
  summaryData = data;
  $("#total").textContent = data.cases;
  $("#nav-count").textContent = data.cases;
  $("#completed").textContent = data.statuses.completed || 0;
  $("#pending").textContent =
    (data.statuses.queued || 0) + (data.statuses.processing || 0);
  $("#components").textContent = data.graph.snapshot.component_count;
  $("#queue-summary").textContent =
    t("Всего {total} · обработано {completed}", {total: data.cases, completed: data.statuses.completed || 0});
  const tracks = $("#track-summary");
  tracks.replaceChildren();
  for (const track of ["lottery", "casino", "investment"]) {
    const counts = trackCounts(data.levels, track),
      total = Object.values(counts).reduce((a, b) => a + b, 0);
    const row = node("div", undefined, "track-summary-row");
    row.append(node("h3", label(track)));
    const bar = node("div", undefined, "risk-bar");
    bar.setAttribute("aria-hidden", "true");
    const legend = node("div", undefined, "risk-legend");
    for (const level of ["high", "medium", "low"]) {
      const segment = node("span", undefined, level);
      segment.style.width = (total ? (100 * counts[level]) / total : 0) + "%";
      bar.append(segment);
      legend.append(node("span", `${label(level)}: ${counts[level]}`));
    }
    row.append(bar, legend);
    tracks.append(row);
  }
  const decisions = $("#decision-summary");
  decisions.replaceChildren();
  for (const action of ["confirm_signs", "false_positive", "request_review"]) {
    const row = node("div", undefined, "summary-entry");
    row.append(
      node(
        "span",
        action === "confirm_signs"
          ? t("Признаки подтверждены")
          : action === "request_review"
            ? t("Дополнительная проверка")
            : label(action),
      ),
      node("b", data.latest_decisions[action] || 0),
    );
    decisions.append(row);
  }
  const clusters = $("#cluster-summary");
  clusters.replaceChildren();
  for (const [index, item] of data.graph.top_components.slice(0, 5).entries()) {
    const row = node("div", undefined, "summary-entry");
    row.append(
      node("span", t("Группа {number}", {number: index + 1})),
      node("b", t("{count} мат.", {count: item.cases})),
    );
    clusters.append(row);
  }
  if (!data.graph.top_components.length)
    clusters.append(
      node("p", t("Группы появятся после анализа материалов."), "muted"),
    );
}
function filters() {
  updateFilterCount();
  const params = new URLSearchParams();
  for (const [key, value] of new FormData($("#filters"))) {
    if (!value) continue;
    params.set(
      key,
      key === "since"
        ? new Date(value + "T00:00:00").toISOString()
        : key === "until"
          ? new Date(value + "T23:59:59.999").toISOString()
          : value,
    );
  }
  params.set("limit", "30");
  return params;
}
async function queue(append = false) {
  const generation = ++queueGeneration,
    params = filters();
  if (append && cursor) params.set("cursor", cursor);
  const feedback = $("#queue-feedback");
  feedback.textContent = t("Обновляем материалы…");
  feedback.hidden = false;
  $(".case-list").setAttribute("aria-busy", "true");
  $(".queue-table").inert = true;
  $("#more").disabled = true;
  try {
    const data = await api("/cases?" + params);
    if (generation !== queueGeneration) return;
    queueItems = append ? [...queueItems, ...data.items] : data.items;
    cursor = data.next_cursor;
    renderQueue();
    feedback.hidden = true;
  } catch (error) {
    if (generation !== queueGeneration) return;
    feedback.textContent = t("Не удалось обновить список. Повторите запрос кнопкой обновления.");
    throw error;
  } finally {
    if (generation === queueGeneration) {
      $(".case-list").setAttribute("aria-busy", "false");
      $(".queue-table").inert = false;
      $("#more").disabled = false;
    }
  }
}
function renderQueue() {
  updateFilterCount();
  $("#cases").replaceChildren();
  $("#more").hidden = !cursor;
  for (const item of queueItems) {
    const row = node("tr");
    row.dataset.id = item.case_id;
    if (item.case_id === selected) row.classList.add("selected");
    const first = node("td");
    const button = node(
      "button",
      item.preview
        ? Array.from(item.preview).slice(0, 120).join("") +
            (Array.from(item.preview).length > 120 ? "…" : "")
        : label(item.source_kind) + " · " + item.case_id.slice(0, 8),
      "row-title",
    );
    button.title = item.preview || button.textContent;
    button.setAttribute(
      "aria-label",
      t("Открыть материал {id}", {id: item.case_id.slice(0, 8)}),
    );
    button.onclick = () => openCase(item.case_id).catch(fail);
    first.append(
      button,
      node(
        "span",
        label(item.source_kind) +
          " · " +
          item.case_id.slice(0, 8) +
          " · " +
          new Date(item.created_at).toLocaleDateString(dateLocale()),
        "case-meta",
      ),
    );
    first.querySelector(".case-meta").append(node("span", label(item.language), "mobile-language"));
    if (item.last_action)
      first.append(
        node(
          "span",
          item.last_action === "confirm_signs"
            ? t("Признаки подтверждены")
            : item.last_action === "request_review"
              ? t("Дополнительная проверка")
              : label(item.last_action),
          "case-meta action-meta",
        ),
      );
    const score = node("td", undefined, "score-cell");
    score.append(node("b", item.queue_score === null ? "—" : item.queue_score));
    if (item.queue_level)
      score.append(
        node("span", label(item.queue_level), "badge " + item.queue_level),
      );
    const language = node("td", label(item.language), "language-cell");
    const status = node("td", undefined, "status-cell");
    status.append(
      node("span", label(item.status), "status-label " + item.status),
    );
    const date = node(
      "td",
      new Date(item.created_at).toLocaleDateString(dateLocale()),
      "date-cell",
    );
    date.title = formatDate(item.created_at);
    row.append(first, score, language, status, date);
    row.onclick = (event) => {
      if (!event.target.closest("button,a")) openCase(item.case_id).catch(fail);
    };
    $("#cases").append(row);
  }
  const length = $("#cases").children.length,
    filtered = [...new FormData($("#filters")).values()].some(Boolean);
  $("#queue-empty").hidden = length > 0;
  $(".queue-table").hidden = length === 0;
  $("#list-count").textContent = filtered
    ? t("Результаты фильтрации")
    : t("Очередь проверки");
  $("#loaded-count").textContent = length
    ? t("Показано: {count}{more}", {count: length, more: cursor ? " " + t("· есть ещё") : ""})
    : "";
  $("#reset-filters").hidden = !filtered;
  $("#queue-empty h2").textContent = filtered
    ? t("Совпадений не найдено")
    : t("Пока нет материалов");
  $("#queue-empty p").textContent = filtered
    ? t("Попробуйте изменить условия поиска или сбросить фильтры.")
    : t("Добавьте текст, транскрипт или скриншот для анализа.");
  $("#empty-action").textContent = filtered
    ? t("Сбросить фильтры")
    : t("Добавить материал");
  $("#empty-action").onclick = () =>
    filtered ? resetFilters() : $("#upload").showModal();
}
function source(evidence) {
  const text = detail.prepared?.content.text || "";
  const characters = Array.from(text);
  const view = $("#source");
  view.replaceChildren();
  if (evidence?.span) {
    const { start, end } = evidence.span;
    view.append(
      document.createTextNode(characters.slice(0, start).join("")),
      node("mark", characters.slice(start, end).join("")),
      document.createTextNode(characters.slice(end).join("")),
    );
  } else view.textContent = text;
  $("#image-region")?.remove();
  if (evidence?.region && detail.prepared?.image_width) {
    const box = node("div", undefined, "image-region");
    box.id = "image-region";
    const r = evidence.region;
    Object.assign(box.style, {
      left: (100 * r.x) / detail.prepared.image_width + "%",
      top: (100 * r.y) / detail.prepared.image_height + "%",
      width: (100 * r.width) / detail.prepared.image_width + "%",
      height: (100 * r.height) / detail.prepared.image_height + "%",
    });
    $("#image-wrap").append(box);
  }
  if (evidence)
    $("#source").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
}
function explanations() {
  const target = $("#explanations");
  target.replaceChildren();
  for (const track of result.tracks) {
    const block = node("details");
    block.open = track.score > 0;
    block.append(node("summary", label(track.track)));
    if (!track.features.length)
      block.append(
        node(
          "p",
          t("Учитываемых признаков не выявлено. Это не подтверждение безопасности."),
          "muted",
        ),
      );
    else {
      const table = node("table", undefined, "feature-table"),
        head = node("thead"),
        labels = node("tr");
      labels.append(node("th", t("Признак")), node("th", t("Вклад")));
      head.append(labels);
      table.append(head);
      const body = node("tbody");
      for (const feature of track.features) {
        const row = node(
          "tr",
          undefined,
          feature.contribution < 0 ? "negative" : "",
        );
        const content = node("td");
        content.append(node("div", t(feature.description)));
        for (const evidence of feature.evidence) {
          const button = node("button", "«" + evidence.quote + "»", "evidence");
          button.onclick = () => source(evidence);
          content.append(button);
        }
        row.append(
          content,
          node(
            "td",
            (feature.contribution > 0 ? "+" : "") + feature.contribution,
          ),
        );
        body.append(row);
      }
      table.append(body);
      block.append(table);
    }
    if (track.graph_evidence) {
      const g = track.graph_evidence;
      block.append(
        node(
          "p",
          t("Связи: +{score}. {value}; высокий базовый балл у {high} из {total} предыдущих материалов.", {score: track.graph_adjustment, value: g.value, high: g.high_base_cases, total: g.previous_cases}),
        ),
      );
      const button = node("button", t("Открыть связанный материал"), "secondary");
      button.onclick = () => openCase(g.seed_case_id).catch(fail);
      block.append(button);
    }
    if (track.cap_adjustment)
      block.append(
        node("p", t("Ограничение шкалы 0–100: {score}.", {score: track.cap_adjustment}), "muted"),
      );
    target.append(block);
  }
}
const svgNS = "http://www.w3.org/2000/svg";
function svg(tag, attributes) {
  const element = document.createElementNS(svgNS, tag);
  for (const [key, value] of Object.entries(attributes))
    element.setAttribute(key, String(value));
  return element;
}
async function loadGraphChoices(append = false) {
  if (graphLoaded && !append) return;
  const query = new URLSearchParams({ limit: "100" });
  if (append && graphCursor) query.set("cursor", graphCursor);
  const data = await api("/cases?" + query);
  if (!append) graphRecords.clear();
  for (const item of data.items) graphRecords.set(item.case_id, item);
  graphCursor = data.next_cursor;
  graphLoaded = true;
  $("#graph-more").hidden = !graphCursor;
  const selector = $("#graph-case");
  selector.replaceChildren(node("option", t("Выберите материал")));
  selector.firstChild.value = "";
  for (const item of graphRecords.values()) {
    const preview = item.preview
      ? Array.from(item.preview).slice(0, 70).join("") + (Array.from(item.preview).length > 70 ? "…" : "")
      : label(item.source_kind);
    const option = node(
      "option",
      `${preview} · #${item.case_id.slice(0, 8)}${item.queue_score === null ? t(" · без результата") : " · " + item.queue_score}`,
    );
    option.value = item.case_id;
    selector.append(option);
  }
}
async function graph(caseId = graphCaseId) {
  graphCaseId = caseId;
  const generation = ++graphGeneration;
  $("#graph").replaceChildren(node("div", t("Загрузка графа…"), "loading-state"));
  $("#graph-inspector").replaceChildren(node("p", t("Загрузка графа…"), "muted"));
  $("#graph-context").textContent = "";
  $("#graph-bounds").textContent = "";
  graphModel = null;
  graphTimeline = [];
  if (!caseId) {
    $("#graph").replaceChildren(
      node(
        "div",
        t("Добавьте материал и выберите его для просмотра связей."),
        "empty-state",
      ),
    );
    $("#graph-inspector").replaceChildren(
      node("h2", t("Связи материала")),
      node("p", t("Выберите материал, чтобы увидеть его связи."), "muted"),
    );
    $("#graph-case").value = "";
    return;
  }
  $("#graph-case").value = caseId;
  const query = new URLSearchParams({ max_nodes: "60", max_edges: "100" });
  if (graphResultId && graphResultCaseId === caseId)
    query.set("result_id", graphResultId);
  let data;
  try {
    data = await api(`/cases/${caseId}/graph?` + query);
  } catch (error) {
    if (generation !== graphGeneration) return;
    const noResult = error.status === 404 && graphRecords.get(caseId)?.result_id === null;
    const state = node("div", undefined, "empty-state");
    state.append(node("h2", noResult
      ? t("Граф станет доступен после успешного анализа материала.")
      : error.code === "graph_unavailable"
        ? t("Для этой версии анализа граф недоступен.")
        : t("Не удалось загрузить граф.")));
    if (!noResult) state.append(node("p", error.message, "muted"));
    const retry = node("button", t("Повторить загрузку"), "secondary");
    retry.onclick = () => {
      $("#graph-title").focus({ preventScroll: true });
      graph(caseId).catch(fail);
    };
    state.append(retry);
    $("#graph").replaceChildren(state);
    $("#graph-context").textContent = noResult
      ? t("Результат пока недоступен")
      : t("Ошибка загрузки графа");
    $("#graph-inspector").replaceChildren(
      node("h2", t("Связи материала")),
      node("p", t("Повторите загрузку или выберите другой материал."), "muted"),
    );
    return;
  }
  if (generation !== graphGeneration) return;
  graphModel = data.graph;
  graphTimeline = data.timeline || [];
  renderGraph(caseId);
}
function renderGraph(caseId) {
  graphZoom = 1;
  graphOffset = { x: 0, y: 0 };
  const model = graphLayout(
      graphModel,
      caseId,
      matchMedia("(max-width:740px)").matches,
    ),
    picture = svg("svg", {
      viewBox: `0 0 ${model.width} ${model.height}`,
      role: "group",
      "aria-label": t("Граф связей выбранного материала"),
    });
  const scene = svg("g", { "data-scene": "" });
  graphFrame = model;
  picture.append(scene);
  for (const edge of graphModel.edges) {
    const a = model.points.get(edge.source),
      b = model.points.get(edge.target);
    if (!a || !b) continue;
    const line = svg("path", {
      d: graphPath(a, b, model.width === 380),
      class: "graph-edge",
      "data-source": edge.source,
      "data-target": edge.target,
    });
    scene.append(line);
  }
  for (const item of graphModel.nodes) {
    const p = model.points.get(item.node_id),
      focus = item.kind === "case" && item.value === caseId;
    const group = svg("g", {
      transform: `translate(${p.x} ${p.y})`,
      class: `graph-node ${item.kind === "case" ? "case-node" : ""} ${focus ? "focus-node" : ""}`,
      role: "button",
      tabindex: "0",
      "aria-label": `${label(item.kind)}: ${item.value}`,
      "data-node": item.node_id,
    });
    const shape =
      item.kind === "case"
        ? svg("circle", {
            cx: 0,
            cy: 0,
            r: focus ? 27 : 22,
            class: "node-shape",
          })
        : svg("rect", {
            x: -22,
            y: -22,
            width: 44,
            height: 44,
            rx: 11,
            class: "node-shape",
          });
    group.append(svg('rect',{x:-85,y:-35,width:170,height:115,fill:'transparent','pointer-events':'all','aria-hidden':'true'}),shape);
    const symbol = svg("text", {
      x: 0,
      y: 5,
      "text-anchor": "middle",
      class: "node-symbol",
    });
    symbol.textContent =
      item.kind === "case"
        ? t("М")
        : item.kind === "domain"
          ? "↗"
          : item.kind === "promo_code"
            ? "#"
            : item.kind === "template"
              ? t("Т")
              : "•";
    if (item.kind !== "case") symbol.style.fill = "#3f6f54";
    group.append(symbol);
    const nodeLabel = svg("text", { x: 0, y: 49, "text-anchor": "middle" });
    nodeLabel.textContent =
      item.kind === "case"
        ? "#" + item.value.slice(0, 8)
        : item.value.length > 24
          ? item.value.slice(0, 23) + "…"
          : item.value;
    const kind = svg("text", {
      x: 0,
      y: 67,
      "text-anchor": "middle",
      class: "node-kind",
    });
    kind.textContent = focus ? t("Выбранный материал") : label(item.kind);
    const title = svg("title", {});
    title.textContent = item.value;
    group.append(nodeLabel, kind, title);
    group.onclick = () => inspectNode(item);
    group.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        inspectNode(item);
      }
    };
    scene.append(group);
  }
  $("#graph").replaceChildren(picture);
  $("#graph-context").textContent =
    t("Контекст анализа · шаг {step}", {step: graphModel.snapshot.sequence}) +
    (graphModel.total_edges === 0 ? " · " + t("Связей не найдено") : "");
  $("#graph-bounds").textContent =
    t("Показано {nodes} узлов · {edges} из {total} связей{limited}. Связи не означают правовой вывод.", {nodes: graphModel.nodes.length, edges: graphModel.edges.length, total: graphModel.total_edges, limited: graphModel.truncated ? " " + t("· отображение ограничено") : ""});
  const root = graphModel.nodes.find(
    (item) => item.kind === "case" && item.value === caseId,
  );
  if (root) inspectNode(root);
  else $("#graph-inspector").replaceChildren(node("p", t("Связей не найдено"), "muted"));
  let drag = null;
  picture.onpointerdown = (event) => {
    if (event.target.closest(".graph-node")) return;
    drag = { x: event.clientX, y: event.clientY, offset: { ...graphOffset } };
    picture.setPointerCapture(event.pointerId);
  };
  picture.onpointermove = (event) => {
    if (!drag) return;
    const rect = picture.getBoundingClientRect(),
      scale = Math.max(model.width / rect.width, model.height / rect.height);
    graphOffset = {
      x: drag.offset.x + (event.clientX - drag.x) * scale,
      y: drag.offset.y + (event.clientY - drag.y) * scale,
    };
    transformGraph();
  };
  picture.onpointerup = () => {
    drag = null;
  };
  picture.onpointercancel = () => {
    drag = null;
  };
}
function transformGraph() {
  const scene = $("#graph [data-scene]");
  if (scene)
    scene.setAttribute(
      "transform",
      `translate(${(graphFrame.width / 2) * (1 - graphZoom) + graphOffset.x} ${(graphFrame.height / 2) * (1 - graphZoom) + graphOffset.y}) scale(${graphZoom})`,
    );
}
function inspectNode(item) {
  const related = new Set([item.node_id]);
  for (const edge of graphModel.edges)
    if (edge.source === item.node_id || edge.target === item.node_id) {
      related.add(edge.source);
      related.add(edge.target);
    }
  for (const edge of document.querySelectorAll(".graph-edge")) {
    const active =
      edge.dataset.source === item.node_id ||
      edge.dataset.target === item.node_id;
    edge.classList.toggle("active", active);
    edge.style.opacity = active ? "1" : ".35";
  }
  for (const group of document.querySelectorAll(".graph-node")) {
    group.classList.toggle(
      "selected-node",
      group.dataset.node === item.node_id,
    );
    group.style.opacity = related.has(group.dataset.node) ? "1" : ".4";
  }
  const inspector = $("#graph-inspector");
  inspector.replaceChildren(
    node("h2", label(item.kind)),
    node(
      "p",
      item.kind === "case" ? "#" + item.value.slice(0, 8) : item.value,
      "node-value",
    ),
    node("p", t("Первое наблюдение: шаг {step}", {step: item.first_seen}), "muted"),
  );
  if (item.kind === "case") {
    const button = node("button", t("Открыть материал"), "secondary");
    button.onclick = () => openCase(item.value).catch(fail);
    inspector.append(button);
  }
  const list = node("div", undefined, "graph-related");
  for (const other of graphModel.nodes) {
    if (!related.has(other.node_id) || other.node_id === item.node_id) continue;
    const button = node(
      "button",
      other.kind === "case"
        ? t("Материал #{id}", {id: other.value.slice(0, 8)})
        : label(other.kind) + ": " + other.value,
      "secondary",
    );
    button.onclick = () => inspectNode(other);
    list.append(button);
  }
  inspector.append(
    node("h3", t("Связанные узлы · {count}", {count: Math.max(0, related.size - 1)})),
    list,
  );
  const origins = [...new Set(graphModel.edges.filter(edge => edge.source === item.node_id || edge.target === item.node_id).map(edge => label(edge.origin)))];
  if (origins.length) inspector.append(node("p", origins.join(" · "), "muted"));
  if (graphTimeline.length) {
    inspector.append(node("h3", t("Хронология материалов")));
    const timeline = node("ol", undefined, "history");
    for (const entry of graphTimeline) timeline.append(node("li", `${formatDate(entry.created_at)} · #${entry.case_id.slice(0, 8)}`));
    inspector.append(timeline);
  }
}
function showView(view) {
  activeView = view;
  for (const name of ["materials", "graph", "overview"])
    $("#" + name + "-view").hidden = name !== view;
  for (const link of document.querySelectorAll("[data-view]")) {
    if (link.dataset.view === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const title = {
    materials: t("Материалы"),
    graph: t("Граф связей"),
    overview: t("Сводка"),
  }[view];
  $("#breadcrumb").textContent = title;
  document.title = "SafeNet KZ · " + title;
  if (view !== "materials") setDetailsOpen(false);
}
async function applyRoute() {
  const generation = ++routeGeneration;
  const route = readRoute(location.hash);
  showView(route.view);
  ++graphGeneration;
  if (route.view !== "materials" || !route.caseId) ++detailGeneration;
  if (route.view === "materials") {
    if (route.caseId && detail?.case.case_id !== route.caseId)
      await openCase(route.caseId, { route: false });
    else setDetailsOpen(Boolean(route.caseId));
  } else if (route.view === "graph") {
    await loadGraphChoices();
    if (generation !== routeGeneration) return;
    const id =
      route.caseId ||
      graphCaseId ||
      selected ||
      graphRecords.keys().next().value;
    if (id && !graphRecords.has(id)) {
      const data = await api("/cases/" + id);
      if (generation !== routeGeneration) return;
      const option = node(
        "option",
        `${label(data.case.source_kind)} · ${id.slice(0, 8)}`,
      );
      option.value = id;
      $("#graph-case").append(option);
    }
    await graph(id);
  }
}
function navigate(view, caseId = null) {
  history.pushState(null, "", routeHash(view, caseId));
  applyRoute().catch(fail);
}
function setDetailsOpen(open) {
  $("#detail-shell").hidden = !open;
  $("#workspace").classList.toggle("has-detail", open);
  const modal = open && matchMedia("(max-width:1240px)").matches;
  document.body.classList.toggle("detail-open", modal);
  for (const target of [
    ".sidebar",
    ".topbar",
    "#filters",
    "#materials-view>.page-heading",
    ".case-list",
  ])
    $(target).inert = modal;
  if (modal) {
    $("#detail-shell").setAttribute("role", "dialog");
    $("#detail-shell").setAttribute("aria-modal", "true");
    $("#detail-shell").setAttribute("aria-label", t("Карточка материала"));
  } else {
    for (const name of ["role", "aria-modal", "aria-label"])
      $("#detail-shell").removeAttribute(name);
  }
}
function setFiltersExpanded(expanded) {
  $("#extra-filters").hidden = !expanded;
  $("#filters").dataset.expanded = String(expanded);
  $("#toggle-filters").setAttribute("aria-expanded", String(expanded));
}
function updateFilterCount() {
  const count = [...new FormData($("#filters")).values()].filter(Boolean).length;
  $("#filter-count").textContent = count;
  $("#filter-count").hidden = count === 0;
  $("#reset-filters").hidden = count === 0;
  $("#toggle-filters").setAttribute("aria-label", count
    ? t("Ещё фильтры, активно: {count}", {count})
    : t("Ещё фильтры"));
}
function resetFilters() {
  $("#filters").reset();
  $("#filters").elements.until.setCustomValidity("");
  queue().catch(fail);
}

async function decisionHistory(generation) {
  const data = await api(`/cases/${selected}/decisions?limit=200`);
  if (generation !== detailGeneration) return;
  const list = $("#history");
  list.replaceChildren();
  for (const item of data.items)
    list.append(
      node(
        "li",
        t("{date} — {action} · результат {id}", {date: formatDate(item.created_at), action: label(item.action), id: item.result_id.slice(0, 8)}),
      ),
    );
  if (!data.items.length) list.append(node("li", t("Решений пока нет.")));
  if (data.next_cursor)
    list.append(
      node(
        "li",
        t("Показаны первые 200 решений; полная история доступна в экспорте."),
      ),
    );
}
async function openCase(id, { route = true } = {}) {
  ++routeGeneration;
  ++graphGeneration;
  showView("materials");
  if (route && location.hash !== routeHash("materials", id))
    history.pushState(null, "", routeHash("materials", id));
  selected = id;
  detail = null;
  result = null;
  const generation = ++detailGeneration;
  setDetailsOpen(true);
  const target = $("#detail");
  target.replaceChildren(node("p", t("Загрузка материала…"), "loading-state"));
  $("#detail-shell").scrollTop = 0;
  $("#close-detail").focus({preventScroll: true});
  let data;
  try {
    data = await api("/cases/" + id);
  } catch (error) {
    if (generation !== detailGeneration) return;
    const retry = node("button", t("Повторить загрузку"), "secondary");
    retry.onclick = () => openCase(id, {route: false}).catch(fail);
    target.replaceChildren(node("p", error.message, "form-error"), retry);
    return;
  }
  if (generation !== detailGeneration || activeView !== "materials" || readRoute(location.hash).caseId !== id) return;
  detail = data;
  result = data.latest_result;
  setDetailsOpen(true);
  target.replaceChildren();
  const title = node("div", undefined, "section-head");
  const jump = node("button", t("К решению"), "text-button detail-jump");
  jump.disabled = !result;
  jump.onclick = () => {
    actions.focus({ preventScroll: true });
    actions.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  };
  title.append(node("h2", `${label(data.case.source_kind)} · ${label(data.case.status)}`), jump);
  target.append(
    node("small", t("Материал #{id}", {id: id.slice(0, 8)})),
    title,
    node(
      "p",
      `${label(data.case.language)} · ${formatDate(data.case.created_at)}`,
      "muted",
    ),
  );
  const heading = node("div", undefined, "section-head");
  target.querySelector("small").title = id;
  heading.append(node("h3", t("Оценка риска")));
  const versions = node("select");
  versions.setAttribute("aria-label", t("Версия анализа"));
  heading.append(versions);
  target.append(heading);
  const cards = node("div", undefined, "tracks");
  cards.id = "tracks";
  target.append(cards);
  target.append(node("h3", t("Обезличенный источник")));
  const text = node("div", undefined, "source");
  text.id = "source";
  target.append(text);
  if (detail.prepared?.image_width) {
    const wrap = node("div", undefined, "image-wrap");
    wrap.id = "image-wrap";
    const image = node("img");
    image.src = `/backend/cases/${id}/image`;
    image.alt = t("Реконструкция из обезличенного распознанного текста");
    wrap.append(image);
    target.append(
      wrap,
      node("p", t("Исходные пиксели и метаданные не сохраняются."), "muted"),
    );
  }
  target.append(node("h3", t("Объяснения")));
  const explanation = node("div");
  explanation.id = "explanations";
  target.append(explanation);
  const entities = node("details");
  entities.append(node("summary", t("Извлечённые сущности")));
  for (const entity of detail.prepared?.entities || [])
    entities.append(
      node("p", `${label(entity.kind) || entity.kind}: ${entity.value}`),
    );
  target.append(entities);
  if (detail.prepared?.network?.group_id) {
    target.append(node("p", t("Роль в группе: {role}", {role: label(detail.prepared.network.role)}), "muted"));
  }
  const graphLink = node("div", undefined, "case-graph-link");
  graphLink.append(node("span", t("Домены, аккаунты и общие признаки")));
  const graphButton = node("button", t("Открыть связи"), "secondary");
  graphButton.onclick = () => {
    graphResultId = result?.result_id || null;
    graphResultCaseId = id;
    navigate("graph", id);
  };
  graphLink.append(graphButton);
  target.append(graphLink);
  const decisionTitle = node("h3", t("Решение аналитика"), "decision-section");
  decisionTitle.id = "decision-title";
  target.append(decisionTitle);
  const actions = node("div", undefined, "actions decision-actions");
  actions.id = "decision-actions";
  actions.tabIndex = -1;
  actions.setAttribute("role", "group");
  actions.setAttribute("aria-labelledby", "decision-title");
  for (const action of ["confirm_signs", "false_positive", "request_review"]) {
    const button = node("button", label(action), "secondary");
    button.disabled = !result;
    button.onclick = async () => {
      button.disabled = true;
      try {
        await api(`/cases/${id}/decisions`, {
          request_id: crypto.randomUUID(),
          result_id: result.result_id,
          action,
        });
        message(t("Решение сохранено. Оценки не изменены."));
        await decisionHistory(generation);
        await queue();
        await overview();
      } catch (error) {
        fail(error);
      } finally {
        button.disabled = false;
      }
    };
    actions.append(button);
  }
  target.append(actions);
  const reanalyze = node("button", t("Повторить анализ"), "secondary");
  reanalyze.disabled = !detail.prepared;
  reanalyze.onclick = async () => {
    reanalyze.disabled = true;
    try {
      const response = await api(`/cases/${id}/reanalyze`, {
        request_id: crypto.randomUUID(),
      });
      message(t("Новая версия поставлена в очередь."));
      await waitJobs(response.jobs);
      if (activeView === "materials" && selected === id)
        await openCase(id, {route: false});
      await refresh();
    } catch (error) {
      fail(error);
      reanalyze.disabled = false;
    }
  };
  target.append(reanalyze);
  if (data.case.error_code)
    target.append(
      node(
        "p",
        t(errors[data.case.error_code] || "") || t("Код ошибки: {code}", {code: data.case.error_code}),
      ),
    );
  const list = node("ol", undefined, "history");
  list.id = "history";
  target.append(list);
  source();
  if (result) {
    renderResult();
    const data = await api(`/cases/${id}/results?limit=200`);
    if (generation !== detailGeneration) return;
    for (const version of data.items) {
      const option = node(
        "option",
        t("Версия {version} · {date}", {version: version.version, date: new Intl.DateTimeFormat(dateLocale(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(version.created_at))}),
      );
      option.value = version.version;
      option.title = formatDate(version.created_at);
      versions.append(option);
    }
    versions.onchange = async () => {
      try {
        const version = versions.value;
        const loaded = await api(`/cases/${id}/results/${version}`);
        if (generation !== detailGeneration || versions.value !== version) return;
        result = loaded;
        renderResult();
        graphResultId = loaded.result_id;
        graphResultCaseId = id;
      } catch (error) {
        fail(error);
      }
    };
    renderResult();
  } else {
    cards.append(node("p", t("Результат пока отсутствует."), "muted"));
    versions.disabled = true;
  }
  await decisionHistory(generation);
  if (generation !== detailGeneration) return;
  for (const row of document.querySelectorAll("#cases tr"))
    row.classList.toggle("selected", row.dataset.id === id);
  target.focus({ preventScroll: true });
}
function renderResult() {
  const cards = $("#tracks");
  cards.replaceChildren();
  for (const track of result.tracks) {
    const card = node("div", undefined, "track");
    card.append(
      node("span", label(track.track)),
      node("strong", track.score),
      node("span", label(track.level), "badge " + track.level),
    );
    cards.append(card);
  }
  explanations();
}
async function waitJobs(jobs) {
  const until = Date.now() + 90000;
  while (Date.now() < until) {
    const values = await Promise.all(
      jobs.map((item) => api("/jobs/" + item.job_id)),
    );
    if (
      values.every(
        (value) => !["queued", "processing"].includes(value.job.status),
      )
    )
      return values;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  message(t("Обработка продолжается на сервере. Обновите очередь позже."));
  return null;
}
async function refresh() {
  await Promise.all([queue(), overview()]);
}
$("#refresh").onclick = () => refresh().catch(fail);
$("#more").onclick = () => queue(true).catch(fail);
$("#filters").onsubmit = (event) => {
  event.preventDefault();
  const {since, until} = $("#filters").elements;
  until.setCustomValidity(since.value && until.value && since.value > until.value ? t("Дата окончания должна быть не раньше даты начала.") : "");
  if (!until.validity.valid) setFiltersExpanded(true);
  if (!$("#filters").reportValidity()) return;
  queue().catch(fail);
};
$("#filters").onchange = () => {
  updateFilterCount();
  $("#filters").elements.until.setCustomValidity("");
  $("#filters").requestSubmit();
};
$("#open-upload").onclick = () => {
  $("#upload-error").hidden = true;
  $("#upload").showModal();
};
$("#toggle-filters").onclick = () => {
  setFiltersExpanded($("#extra-filters").hidden);
};
$("#reset-filters").onclick = resetFilters;
$("#close-detail").onclick = () => {
  navigate("materials");
  document.querySelector(`#cases tr[data-id="${selected}"] .row-title`)?.focus();
};
$("#graph-case").onchange = () => {
  graphResultId = null;
  navigate("graph", $("#graph-case").value || null);
};
$("#graph-more").onclick = () => loadGraphChoices(true).catch(fail);
$("#graph-refresh").onclick = () => graph().catch(fail);
$("#overview-refresh").onclick = () => overview().catch(fail);
$("#zoom-in").onclick = () => {
  graphZoom = limitZoom(graphZoom + 0.2);
  transformGraph();
};
$("#zoom-out").onclick = () => {
  graphZoom = limitZoom(graphZoom - 0.2);
  transformGraph();
};
$("#zoom-fit").onclick = () => {
  graphZoom = 1;
  graphOffset = { x: 0, y: 0 };
  transformGraph();
};
addEventListener("hashchange", () => applyRoute().catch(fail));
addEventListener("popstate", () => applyRoute().catch(fail));
matchMedia("(max-width:740px)").addEventListener("change", () => {
  if (activeView === "graph" && graphModel && $("#graph svg"))
    renderGraph(graphCaseId);
});
addEventListener("resize", () =>
  setDetailsOpen(activeView === "materials" && !$("#detail-shell").hidden),
);
addEventListener("keydown", (event) => {
  if ($("#upload").open || $("#detail-shell").hidden)
    return;
  if (event.key === "Escape") {
    event.preventDefault();
    $("#close-detail").click();
    return;
  }
  if (event.key === "Tab" && document.body.classList.contains("detail-open")) {
    const items = [
      ...$("#detail-shell").querySelectorAll('button,a,select,[tabindex="0"]'),
    ].filter((item) => !item.disabled && item.offsetParent !== null);
    const first = items[0],
      last = items.at(-1);
    if (
      event.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === $("#detail"))
    ) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
});
$("#close-upload").onclick = () => $("#upload").close();
const form = $("#upload-form");
form.elements.kind.onchange = () => {
  const kind = form.elements.kind.value;
  $("#text-label").hidden = !["text", "transcript"].includes(kind);
  $("#file-label").hidden = ["text", "transcript"].includes(kind);
  form.elements.file.accept =
    kind === "screenshot"
      ? ".png,.jpg,.jpeg,.webp"
      : kind === "csv"
        ? ".csv"
        : ".jsonl";
  delete form.dataset.request;
  updateUploadHint();
};
function updateUploadHint() {
  const kind = form.elements.kind.value;
  $("#upload-hint").textContent = ["text", "transcript"].includes(kind)
    ? t("{count} / 100 000 символов", {count: form.elements.text.value.length.toLocaleString(dateLocale())})
    : kind === "screenshot" ? t("PNG, JPEG или WebP · до 2,9 МБ") : t("Файл до 4 МБ");
  $("#upload-note").textContent = kind === "screenshot"
    ? t("Используйте синтетические или обезличенные данные. Изображение сохраняется как обезличенная OCR-реконструкция.")
    : ["jsonl", "csv"].includes(kind)
      ? t("Используйте синтетические или обезличенные данные. Формат файла: {format}.", { format: kind.toUpperCase() })
      : t("Используйте синтетические или обезличенные данные. Вставьте текст публикации или готовый транскрипт.");
}
form.oninput = () => {
  delete form.dataset.request;
  updateUploadHint();
};
form.onsubmit = async (event) => {
  event.preventDefault();
  const uploadRouteGeneration = routeGeneration;
  const submit = $("#submit-upload");
  submit.disabled = true;
  submit.textContent = t("Отправка…");
  $("#upload-error").hidden = true;
  try {
    const kind = form.elements.kind.value;
    const requestId = form.dataset.request || crypto.randomUUID();
    form.dataset.request = requestId;
    let response;
    if (["jsonl", "csv"].includes(kind)) {
      const file = form.elements.file.files[0];
      if (!file) throw Error(t("Выберите файл."));
      if (file.size > 4000000) throw Error(t("Файл превышает 4 000 000 байт."));
      const raw = await fetch(
        `/backend/ingestions/file?request_id=${requestId}&format=${kind}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        },
      );
      response = await raw.json();
      if (!raw.ok)
        throw Error(
          t(errors[response.error_code] || "") ||
            t("Загрузка отклонена: {code}", {code: response.error_code || raw.status}),
        );
    } else {
      const provenance = form.elements.provenance.value;
      const record = {
        source_kind: kind,
        provenance,
        synthetic: provenance === "synthetic",
        text: form.elements.text.value,
      };
      if (kind === "screenshot") {
        const file = form.elements.file.files[0];
        if (!file) throw Error(t("Выберите изображение."));
        if (file.size > 2900000)
          throw Error(
            t("Изображение слишком велико после Base64. Используйте файл до 2,9 MB."),
          );
        const encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result.split(",")[1]);
          reader.onerror = () => reject(Error(t("Не удалось прочитать файл.")));
          reader.readAsDataURL(file);
        });
        record.text = "";
        record.image_base64 = encoded;
        record.image_format = /\.png$/i.test(file.name)
          ? "png"
          : /\.jpe?g$/i.test(file.name)
            ? "jpeg"
            : "webp";
      } else if (!record.text.trim()) throw Error(t("Введите текст."));
      response = await api("/ingestions", {
        request_id: requestId,
        records: [record],
      });
    }
    delete form.dataset.request;
    $("#upload").close();
    form.elements.text.value = "";
    form.elements.file.value = "";
    message(t("Принято заданий: {count}. Выполняется обработка…", {count: response.jobs.length}));
    await refresh();
    const finished = await waitJobs(response.jobs);
    await refresh();
    if (finished) {
      const successful = finished.filter(
        (value) => value.job.status === "completed",
      ).length;
      message(
        t("Обработка завершена: {success} из {total}. Ошибки доступны в карточках.", {success: successful, total: finished.length}),
        successful !== finished.length,
      );
      if (uploadRouteGeneration === routeGeneration)
        await openCase(response.jobs[0].case_id);
    }
  } catch (error) {
    if ($("#upload").open) {
      $("#upload-error").textContent = error.message;
      $("#upload-error").hidden = false;
      $("#upload-error").scrollIntoView({block: "nearest"});
    } else fail(error);
  } finally {
    submit.disabled = false;
    submit.textContent = t("Отправить на анализ");
    updateUploadHint();
  }
};
for (const button of document.querySelectorAll("[data-locale]")) {
  button.onclick = async () => {
    const previousVersion = result?.version;
    const previousCase = detail?.case.case_id;
    setLocale(button.dataset.locale);
    localizeStatic();
    const route = readRoute(location.hash);
    showView(route.view);
    if (summaryData) renderOverview(summaryData);
    if (!$("#overview-feedback").hidden) overview();
    renderQueue();
    updateUploadHint();
    message("");
    graphLoaded = false;
    for (const item of document.querySelectorAll("[data-locale]")) item.disabled = true;
    try {
      if (route.view === "materials" && route.caseId) {
        await openCase(route.caseId, {route: false});
        const generation = detailGeneration;
        if (!detail || readRoute(location.hash).caseId !== route.caseId || activeView !== "materials") return;
        if (previousCase === route.caseId && previousVersion && previousVersion !== result?.version) {
          const loaded = await api(`/cases/${route.caseId}/results/${previousVersion}`);
          if (generation !== detailGeneration) return;
          result = loaded;
          $("#detail select").value = previousVersion;
          renderResult();
        }
      } else await applyRoute();
    } catch (error) { fail(error); }
    finally { for (const item of document.querySelectorAll("[data-locale]")) item.disabled = false; }
  };
}
updateUploadHint();
showView(readRoute(location.hash).view);
refresh()
  .catch(fail);
applyRoute().catch(fail);
