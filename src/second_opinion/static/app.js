"use strict";

const $ = (id) => document.getElementById(id);

const FACT_LABELS = {
  qualification: "Квалификация",
  defendant_age: "Возраст подсудимого",
  prior_convictions: "Наличие судимостей",
  minor_dependents: "Несовершеннолетние дети",
  health_factor: "Состояние здоровья",
  offense_stage: "Стадия преступления",
  group_offense: "Групповой характер деяния",
  guilty_plea: "Признание вины",
  surrender_or_confession: "Явка с повинной / способствование расследованию",
  restitution: "Возмещение ущерба / заглаживание вреда",
  aggravating_recidivism: "Рецидив (отягчающее)",
  special_procedure: "Особый порядок судопроизводства",
  jury_trial: "Суд присяжных",
  punishment_type: "Вид наказания",
  punishment_term: "Размер наказания (мес.)",
  suspended_sentence: "Условное осуждение",
  date_of_offense: "Дата совершения",
};

const STAGE_LABELS = {
  completed: "оконченное",
  attempt: "покушение",
  preparation: "приготовление",
};

// Управление вводом значения для ручного добавления факта.
const FACT_VALUE_KINDS = {
  qualification: "qualification",
  defendant_age: "number",
  punishment_term: "number",
  punishment_type: {
    imprisonment: "лишение свободы",
    fine: "штраф",
    correctional_labor: "исправительные работы",
    compulsory_labor: "обязательные работы",
    restriction_of_liberty: "ограничение свободы",
    other: "иное",
  },
  offense_stage: STAGE_LABELS,
  group_offense: {
    group_of_persons: "группой лиц",
    group_with_conspiracy: "группой лиц по предварительному сговору",
    organized_group: "организованной группой",
  },
  date_of_offense: "date",
  health_factor: "text",
};

function isBooleanFactType(type) {
  return FACT_VALUE_KINDS[type] === undefined && type !== "qualification";
}

let newValueAccessor = () => null;

function buildValueInput(type) {
  const container = $("new-fact-value-input");
  container.innerHTML = "";
  const kind = FACT_VALUE_KINDS[type];
  if (type === "qualification") {
    container.innerHTML =
      '<input type="number" id="nf-article" min="1" placeholder="статья" style="width:7em"> ' +
      '<input type="number" id="nf-part" min="1" placeholder="часть (необязательно)" style="width:12em">';
    newValueAccessor = () => {
      const article = Number($("nf-article").value);
      if (!article) return null;
      const partRaw = $("nf-part").value;
      return { article, part: partRaw ? Number(partRaw) : null };
    };
    return;
  }
  if (typeof kind === "object") {
    const select = document.createElement("select");
    select.id = "nf-value";
    Object.entries(kind).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    });
    container.appendChild(select);
    newValueAccessor = () => select.value;
    return;
  }
  if (isBooleanFactType(type)) {
    const select = document.createElement("select");
    select.id = "nf-value";
    [["true", "да"], ["false", "нет"]].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    });
    container.appendChild(select);
    newValueAccessor = () => select.value === "true";
    return;
  }
  const input = document.createElement("input");
  input.type = kind === "number" ? "number" : kind === "date" ? "date" : "text";
  input.id = "nf-value";
  if (kind === "number") input.min = "0";
  container.appendChild(input);
  newValueAccessor = () => {
    if (kind === "number") return input.value === "" ? null : Number(input.value);
    return input.value;
  };
}

let documentText = "";

function clearError() {
  $("upload-error").hidden = true;
}

function showError(message) {
  const box = $("upload-error");
  box.textContent = message;
  box.hidden = false;
}

function badge(status) {
  const span = document.createElement("span");
  span.className = `status-badge status-${status}`;
  span.textContent =
    status === "PASS" ? "✓" : status === "WARNING" ? "⚠" : status === "FAIL" ? "✗" : "?";
  span.title = status;
  // Скринридер должен слышать статус, а не символ.
  span.setAttribute("role", "img");
  span.setAttribute("aria-label", status);
  return span;
}

function toggleDetail(detail, trigger) {
  const hidden = detail.classList.toggle("hidden");
  if (trigger) trigger.setAttribute("aria-expanded", String(!hidden));
}

// Раскрытие списка: клик мышью + Enter/Space с клавиатуры (a11y).
function wireDisclosure(title, detail) {
  title.setAttribute("role", "button");
  title.setAttribute("tabindex", "0");
  title.setAttribute("aria-expanded", "false");
  const toggle = () => toggleDetail(detail, title);
  title.addEventListener("click", toggle);
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  });
}

function div(className, text) {
  const el = document.createElement("div");
  el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

// Единая точка обращения к API: подставляет токен (если задан) и
// показывает форму входа при 401.
function apiFetch(url, options = {}) {
  const token = sessionStorage.getItem("so-token");
  const headers = { ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(url, { ...options, headers }).then((response) => {
    if (response.status === 401) $("auth-box").hidden = false;
    return response;
  });
}

function factValueLabel(fact) {
  const value = fact.value;
  if (fact.type === "qualification" && typeof value === "object" && value !== null) {
    const part = value.part ? ` ч. ${value.part}` : "";
    return `ст. ${value.article}${part} ${value.code || "УК РФ"}`;
  }
  if (fact.type === "offense_stage") return STAGE_LABELS[value] || value;
  if (typeof value === "boolean") return value ? "да" : "нет";
  return String(value);
}

async function updateFact(analysisId, factId, body) {
  clearError();
  const response = await apiFetch(`/api/analyses/${analysisId}/facts/${factId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    showError(`Ошибка правки факта: ${(await response.json()).detail || response.status}`);
    return;
  }
  renderReport(await response.json());
}

function renderFact(fact, analysisId, usedIn) {
  const li = document.createElement("li");
  const title = div("fact-title");
  title.appendChild(badge(fact.status));
  title.append(` ${FACT_LABELS[fact.type] || fact.type}: ${factValueLabel(fact)}`);

  const detail = div("detail hidden");
  const meta = div("meta");
  meta.appendChild(div("", `Статус: ${fact.status}`));
  meta.appendChild(div("", `Метод извлечения: ${fact.extraction_method}`));
  meta.appendChild(div("", `Уверенность: ${fact.confidence}`));
  if (fact.prompt_version) meta.appendChild(div("", `Промпт: ${fact.prompt_version}`));

  fact.evidence.forEach((ev) => {
    const quote = document.createElement("blockquote");
    quote.textContent = ev.quote;
    detail.appendChild(quote);
    detail.appendChild(
      div("meta", `Фрагмент документа: символы ${ev.start_offset}–${ev.end_offset}` +
        (ev.page ? `, страница ${ev.page}` : ""))
    );
  });

  // Обратная связь: в каких проверках правила-движка участвует факт.
  const used = usedIn && usedIn[fact.id];
  if (used && used.length) {
    const usedBlock = div("chain");
    usedBlock.appendChild(div("step", "Учитывается при следующих проверках:"));
    used.forEach((evaluation) => {
      const line = div("step", "");
      line.appendChild(badge(evaluation.status));
      line.append(` ${evaluation.headline}`);
      usedBlock.appendChild(line);
    });
    detail.appendChild(usedBlock);
  } else {
    detail.appendChild(
      div("meta", "Ни одна детерминированная проверка не использует этот факт.")
    );
  }

  detail.appendChild(meta);

  const actions = div("meta");
  if (fact.status !== "VERIFIED") {
    const confirmBtn = document.createElement("button");
    confirmBtn.textContent = "Подтвердить";
    confirmBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      updateFact(analysisId, fact.id, { status: "VERIFIED" });
    });
    actions.appendChild(confirmBtn);
  }
  if (fact.status !== "NOT_FOUND") {
    const rejectBtn = document.createElement("button");
    rejectBtn.textContent = "Исключить из проверок";
    rejectBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      updateFact(analysisId, fact.id, { status: "NOT_FOUND" });
    });
    actions.appendChild(rejectBtn);
  }
  if (actions.children.length) {
    detail.appendChild(div("meta", "Решение судьи:"));
    detail.appendChild(actions);
  }

  wireDisclosure(title, detail);
  li.appendChild(title);
  li.appendChild(detail);
  return li;
}

function renderEvaluation(ev, factsById) {
  const li = document.createElement("li");
  const title = div("eval-title");
  title.appendChild(badge(ev.status));
  title.append(` ${ev.headline}`);

  const detail = div("detail hidden");
  detail.appendChild(div("chain", ""));
  detail.lastChild.appendChild(div("step", ev.explanation));

  if (ev.facts_used.length) {
    const factsBlock = div("chain");
    factsBlock.appendChild(div("step", "Использованные факты:"));
    ev.facts_used.forEach((factId) => {
      const fact = factsById[factId];
      const label = fact
        ? `${FACT_LABELS[fact.type] || fact.type}: ${factValueLabel(fact)}`
        : factId;
      factsBlock.appendChild(div("step", `← ${label}`));
    });
    detail.appendChild(factsBlock);
  }

  if (ev.norms_used.length) {
    const normsBlock = div("chain");
    normsBlock.appendChild(div("step", "Применённые нормы:"));
    ev.norms_used.forEach((norm) => {
      const versionLabel = norm.version_id ? ` (редакция: ${norm.version_id})` : "";
      normsBlock.appendChild(div("step", `→ ${norm.ref}${versionLabel}`));
    });
    detail.appendChild(normsBlock);
  }

  if (ev.missing.length) {
    const missingBlock = div("missing");
    missingBlock.textContent = `Не хватает данных: ${ev.missing.join("; ")}`;
    detail.appendChild(missingBlock);
  }

  if (Object.keys(ev.numbers).length) {
    const numbers = Object.entries(ev.numbers)
      .map(([k, v]) => `${k}=${v}`)
      .join(", ");
    detail.appendChild(div("numbers", `Расчёт: ${numbers}`));
  }

  detail.appendChild(div("meta", `Правило: ${ev.rule_id} (версия ${ev.rule_version})`));

  wireDisclosure(title, detail);
  li.appendChild(title);
  li.appendChild(detail);
  return li;
}

function renderCase(match) {
  const li = document.createElement("li");
  const c = match.case;
  const title = div("case-title");
  const part = c.part ? ` ч. ${c.part}` : "";
  const suspended = c.suspended ? ", условно" : "";
  const term = c.term_months !== null ? `${c.term_months} мес.` : "—";
  title.textContent =
    `${c.title} — ст. ${c.article}${part} УК РФ; ` +
    `${STAGE_LABELS[c.stage] || c.stage}; наказание: ${term}${suspended}` +
    (c.synthetic ? " (синтетическое дело)" : "");

  const reasons = document.createElement("ul");
  reasons.className = "reasons";
  match.reasons.forEach((reason) => {
    const item = document.createElement("li");
    item.textContent = reason;
    reasons.appendChild(item);
  });

  const detail = div("detail hidden");
  detail.appendChild(div("", `Суд: ${c.court}; дата: ${c.date}`));
  detail.appendChild(div("", c.summary));
  detail.appendChild(reasons);

  wireDisclosure(title, detail);
  li.appendChild(title);
  li.appendChild(detail);
  return li;
}

const FEATURE_LABELS = {
  special_procedure: "Особый порядок",
  guilty_plea: "Признание вины",
  recidivism: "Рецидив",
  jury_trial: "Суд присяжных",
  group: "Групповое деяние",
  suspended: "Условное осуждение",
};

function renderAnalytics(analytics) {
  if (!analytics) {
    $("analytics").textContent = "Недостаточно сопоставимых дел для статистики.";
    return;
  }
  const table = document.createElement("table");
  table.className = "stats";
  const rows = [
    ["Дел в выборке", analytics.n_cases],
    ["Дел с назначенным сроком лишения свободы", analytics.n_with_term],
    ["Медиана срока, мес.", analytics.median_months ?? "—"],
    ["Средний срок, мес.", analytics.mean_months ?? "—"],
    ["25-й перцентиль, мес.", analytics.p25_months ?? "—"],
    ["75-й перцентиль, мес.", analytics.p75_months ?? "—"],
    ["Доля условного осуждения", analytics.suspended_share ?? "—"],
  ];
  rows.forEach(([label, value]) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = label;
    const td = document.createElement("td");
    td.textContent = String(value);
    tr.appendChild(th);
    tr.appendChild(td);
    table.appendChild(tr);
  });
  const dist = document.createElement("div");
  dist.className = "meta";
  dist.textContent = `Виды наказания: ${JSON.stringify(analytics.punishment_type_distribution)}`;

  const spreadTable = document.createElement("table");
  spreadTable.className = "stats";
  const spreadHeader = document.createElement("tr");
  ["Признак в выборке", "Доля дел"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    spreadHeader.appendChild(th);
  });
  spreadTable.appendChild(spreadHeader);
  Object.entries(analytics.feature_spread || {}).forEach(([feature, share]) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = FEATURE_LABELS[feature] || feature;
    const td = document.createElement("td");
    td.textContent = `${(share * 100).toFixed(1)}%`;
    tr.appendChild(th);
    tr.appendChild(td);
    spreadTable.appendChild(tr);
  });

  const note = div("meta", "Статистика описательная и не является рекомендацией о наказании.");
  const wrap = $("analytics");
  wrap.innerHTML = "";
  wrap.appendChild(table);
  wrap.appendChild(dist);
  if (Object.keys(analytics.feature_spread || {}).length) {
    wrap.appendChild(spreadTable);
  }
  wrap.appendChild(note);
}

function renderReport(report) {
  $("results").hidden = false;
  $("download-json").href = `/api/analyses/${report.analysis_id}`;

  const card = $("doc-card");
  card.innerHTML = "";
  const rows = [
    ["Идентификатор документа", report.document_id],
    ["Дата анализа", report.created_at],
    [
      "Дата применения норм",
      report.applicable_at + (report.applicable_at_assumed ? " (предположительно)" : ""),
    ],
    ["Извлечено фактов", report.facts.length],
    ["Проверок выполнено", report.evaluations.length],
    ["Сопоставимых дел", report.comparable_cases.length],
  ];
  rows.forEach(([k, v]) => {
    const row = div("kv");
    row.appendChild(div("k", k));
    row.appendChild(div("v", String(v)));
    card.appendChild(row);
  });

  const factsList = $("facts-list");
  factsList.innerHTML = "";
  // Индекс «факт → проверки», где он использован (обратная связь для судьи).
  const usedIn = {};
  report.evaluations.forEach((ev) => {
    ev.facts_used.forEach((factId) => {
      (usedIn[factId] = usedIn[factId] || []).push(ev);
    });
  });
  const factsById = {};
  report.facts.forEach((fact) => {
    factsById[fact.id] = fact;
    factsList.appendChild(renderFact(fact, report.analysis_id, usedIn));
  });
  if (!report.facts.length) {
    factsList.appendChild(div("hint", "Факты не извлечены."));
  }
  renderAddFactBox(report);

  const evalsList = $("evaluations-list");
  evalsList.innerHTML = "";
  report.evaluations.forEach((ev) => evalsList.appendChild(renderEvaluation(ev, factsById)));

  const casesList = $("cases-list");
  casesList.innerHTML = "";
  report.comparable_cases.forEach((match) => casesList.appendChild(renderCase(match)));
  if (!report.comparable_cases.length) {
    casesList.appendChild(div("hint", "Сопоставимые дела не найдены в базе практики."));
  }

  renderAnalytics(report.analytics);

  const disclaimers = $("disclaimers");
  disclaimers.innerHTML = "";
  report.disclaimers.forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    disclaimers.appendChild(item);
  });
}

function renderAddFactBox(report) {
  const box = $("add-fact-box");
  box.hidden = false;
  box.dataset.analysisId = report.analysis_id;
  const select = $("new-fact-type");
  if (!select.options.length) {
    const existing = new Set(report.facts.map((f) => f.type));
    Object.keys(FACT_LABELS).forEach((type) => {
      if (type === "date_of_offense" && existing.has(type)) return;
      const option = document.createElement("option");
      option.value = type;
      option.textContent = FACT_LABELS[type];
      select.appendChild(option);
    });
    buildValueInput(select.value);
    select.addEventListener("change", () => buildValueInput(select.value));
    $("add-fact-btn").addEventListener("click", submitNewFact);
  }
}

async function submitNewFact() {
  clearError();
  const box = $("add-fact-box");
  const type = $("new-fact-type").value;
  const value = newValueAccessor();
  const quote = $("new-fact-quote").value.trim();
  if (value === null || value === undefined || value === "") {
    showError("Заполните значение обстоятельства.");
    return;
  }
  if (!quote) {
    showError("Вставьте точную цитату из документа.");
    return;
  }
  const response = await apiFetch(`/api/analyses/${box.dataset.analysisId}/facts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, value, quote }),
  });
  if (!response.ok) {
    showError(`Ошибка добавления: ${(await response.json()).detail || response.status}`);
    return;
  }
  $("new-fact-quote").value = "";
  renderReport(await response.json());
}

async function analyzeDocument(docId) {
  const body = {};
  const applicableAt = $("applicable-at").value;
  if (applicableAt) body.applicable_at = applicableAt;
  const response = await apiFetch(`/api/documents/${docId}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error((await response.json()).detail || `HTTP ${response.status}`);
  }
  renderReport(await response.json());
}

async function submit() {
  clearError();
  const btn = $("analyze-btn");
  btn.disabled = true;
  try {
    let docId;
    if (documentText) {
      const response = await apiFetch("/api/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: "pasted_document.txt", text: documentText }),
      });
      if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
      docId = (await response.json()).document_id;
    } else {
      const input = $("doc-file");
      if (!input.files.length) {
        showError("Вставьте текст или выберите файл.");
        return;
      }
      const form = new FormData();
      form.append("file", input.files[0]);
      const response = await apiFetch("/api/documents/upload", { method: "POST", body: form });
      if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
      docId = (await response.json()).document_id;
    }
    await analyzeDocument(docId);
  } catch (err) {
    showError(`Ошибка: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function loadDemo() {
  clearError();
  const response = await fetch("/static/demo_document.txt");
  if (!response.ok) {
    showError("Демо-образец не найден.");
    return;
  }
  documentText = await response.text();
  $("doc-text").value = documentText;
}

function renderSearchHit(hit) {
  const li = document.createElement("li");
  const title = div("eval-title");
  const badgeEl = document.createElement("span");
  badgeEl.className = "status-badge status-LIKELY";
  badgeEl.textContent = hit.score.toFixed(2);
  badgeEl.title = "лексическая релевантность (справочная)";
  title.appendChild(badgeEl);
  title.append(` ${hit.ref} — ${hit.title}`);

  const detail = div("detail hidden");
  const quote = document.createElement("blockquote");
  quote.textContent = hit.fragment;
  detail.appendChild(quote);

  const meta = div("meta");
  const period = hit.effective_to
    ? `${hit.effective_from} — ${hit.effective_to}`
    : `с ${hit.effective_from} (действует)`;
  meta.appendChild(div("", `Редакция: ${hit.version_id}; период действия: ${period}`));
  meta.appendChild(div("", `Источник: ${hit.source_document}`));
  if (hit.source_url) meta.appendChild(div("", `URL: ${hit.source_url}`));
  meta.appendChild(div("", `Извлечено: ${hit.retrieved_at}; SHA-256: ${hit.sha256.slice(0, 16)}…`));
  meta.appendChild(div("", `Статус сверки: ${hit.verification_status}` +
    (hit.synthetic ? "; СИНТЕТИЧЕСКАЯ фикстура" : "")));
  meta.appendChild(div("", `Совпавшие термины: ${hit.matched_terms.join(", ")}`));
  detail.appendChild(meta);

  wireDisclosure(title, detail);
  li.appendChild(title);
  li.appendChild(detail);
  return li;
}

async function runSearch() {
  const box = $("search-error");
  box.hidden = true;
  const query = $("search-query").value.trim();
  const list = $("search-results");
  list.innerHTML = "";
  if (!query) return;
  try {
    const response = await apiFetch(
      `/api/search?q=${encodeURIComponent(query)}&limit=10`
    );
    if (!response.ok) {
      throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    }
    const body = await response.json();
    if (!body.results.length) {
      list.appendChild(div("hint", "В базе источников ничего не найдено."));
      return;
    }
    body.results.forEach((hit) => list.appendChild(renderSearchHit(hit)));
  } catch (err) {
    box.textContent = `Ошибка поиска: ${err.message}`;
    box.hidden = false;
  }
}

$("analyze-btn").addEventListener("click", submit);
$("print-btn").addEventListener("click", () => window.print());
$("auth-save").addEventListener("click", () => {
  const value = $("auth-token").value.trim();
  if (value) {
    sessionStorage.setItem("so-token", value);
    $("auth-token").value = "";
    $("auth-box").hidden = true;
  }
});
$("auth-token").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("auth-save").click();
});
$("demo-btn").addEventListener("click", loadDemo);
$("doc-text").addEventListener("input", (e) => {
  documentText = e.target.value;
});
$("doc-file").addEventListener("change", (e) => {
  documentText = "";
  $("doc-text").value = "";
  $("file-name").textContent = e.target.files.length ? e.target.files[0].name : "или выберите файл…";
});
$("search-btn").addEventListener("click", runSearch);
$("search-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});
