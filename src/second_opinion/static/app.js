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
  final_punishment_term: "Окончательный срок по совокупности (мес.)",
  punishment_hours: "Срок обязательных работ (ч.)",
  fine_amount: "Размер штрафа (руб.)",
  suspended_sentence: "Условное осуждение",
  date_of_offense: "Дата совершения",
  exceptional_circumstances: "Ст. 64 УК РФ упомянута в тексте",
};

const STAGE_LABELS = {
  completed: "оконченное",
  attempt: "покушение",
  preparation: "приготовление",
};

const FACT_VALUE_KINDS = {
  qualification: "qualification",
  defendant_age: "number",
  punishment_term: "number",
  final_punishment_term: "number",
  punishment_hours: "number",
  fine_amount: "number",
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
      '<label class="visually-hidden" for="nf-article">Номер статьи</label>' +
      '<input type="number" id="nf-article" min="1" placeholder="статья" class="fact-input-article"> ' +
      '<label class="visually-hidden" for="nf-part">Номер части (необязательно)</label>' +
      '<input type="number" id="nf-part" min="1" placeholder="часть (необязательно)" class="fact-input-part">';
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
    select.setAttribute("aria-label", "Значение обстоятельства");
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
    select.setAttribute("aria-label", "Значение обстоятельства");
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
  input.setAttribute("aria-label", "Значение обстоятельства");
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

function showFactError(message) {
  const box = $("add-fact-error");
  box.textContent = message;
  box.hidden = false;
}

function clearFactError() {
  $("add-fact-error").hidden = true;
}

function showHistoryError(message) {
  const box = $("history-error");
  box.textContent = message;
  box.hidden = false;
}

function clearHistoryError() {
  $("history-error").hidden = true;
}

const STATUS_BADGES = {
  PASS: ["✓", "проверка пройдена"],
  WARNING: ["⚠", "требует внимания"],
  FAIL: ["✗", "нарушение"],
  UNKNOWN: ["?", "данных недостаточно"],
  NOT_APPLICABLE: ["н/п", "не применимо к этому делу"],
  VERIFIED: ["✓", "подтверждено"],
  LIKELY: ["≈", "вероятно, не подтверждено"],
  UNCERTAIN: ["⚠", "требует проверки"],
  CONFLICT: ["≠", "противоречие в документе"],
  NOT_FOUND: ["—", "исключено из проверок"],
};

function badge(status) {
  const span = document.createElement("span");
  span.className = `status-badge status-${status}`;
  const [glyph, label] = STATUS_BADGES[status] || ["?", status];
  span.textContent = glyph;
  span.title = label;
  span.setAttribute("role", "img");
  span.setAttribute("aria-label", label);
  return span;
}

function statusLabel(status) {
  return (STATUS_BADGES[status] || [null, status])[1];
}

const VERIFICATION_LABELS = {
  verified: "сверено юристом",
  draft: "черновик, юристом не сверен",
};

function verificationLabel(status) {
  return VERIFICATION_LABELS[status] || status;
}

const METHOD_LABELS = {
  pattern: "разбор по шаблонам",
  llm: "языковая модель",
  user: "внесено судьёй",
};

function methodLabel(method) {
  return METHOD_LABELS[method] || method;
}

function toggleDetail(detail, trigger) {
  const hidden = detail.classList.toggle("hidden");
  if (trigger) trigger.setAttribute("aria-expanded", String(!hidden));
}

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

let authMode = "open";

const SESSION_HINT = "so-session";

function setSessionHint(on) {
  try {
    if (on) localStorage.setItem(SESSION_HINT, "1");
    else localStorage.removeItem(SESSION_HINT);
  } catch {
  }
}

function hasSessionHint() {
  try {
    return localStorage.getItem(SESSION_HINT) === "1";
  } catch {
    return false;
  }
}

let refreshing = null;

async function attemptRefresh() {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const response = await fetch("/auth/refresh", {
      method: "POST",
      headers: { "X-SO-CSRF": "1" },
    }).catch(() => null);
    if (response && response.ok) {
      sessionStorage.setItem("so-access", (await response.json()).access_token);
      return true;
    }
    if (!response || response.status !== 401) return false;
  }
  return false;
}

function refreshSession() {
  if (!refreshing) {
    refreshing = attemptRefresh().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

function sendWithToken(url, options) {
  const token = sessionStorage.getItem("so-access") || sessionStorage.getItem("so-token");
  const headers = { ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(url, { ...options, headers });
}

async function apiFetch(url, options = {}) {
  let response = await sendWithToken(url, options);
  const canRefresh =
    response.status === 401 &&
    hasSessionHint() &&
    url !== "/auth/login" &&
    url !== "/auth/refresh";
  if (canRefresh) {
    if (await refreshSession()) {
      response = await sendWithToken(url, options);
    } else {
      sessionStorage.removeItem("so-access");
      setSessionHint(false);
    }
  }
  if (response.status === 401 && !url.startsWith("/auth/")) {
    if (authMode === "accounts") {
      $("account-panel").scrollIntoView({ behavior: "smooth" });
      $("login-email").focus();
    } else {
      $("auth-box").hidden = false;
    }
  }
  return response;
}

async function loadAuthMode() {
  try {
    const response = await fetch("/health");
    if (!response.ok) return;
    authMode = (await response.json()).auth_mode || "open";
    if (authMode === "accounts") {
      $("auth-box").hidden = true;
      $("auth-hint").textContent =
        "Развёртывание принимает только личные учётные записи: " +
        "общий секрет не подойдёт.";
    }
  } catch {
  }
}

function factValueLabel(fact) {
  const value = fact.value;
  if (fact.type === "qualification" && typeof value === "object" && value !== null) {
    const article = value.article_suffix ? `${value.article}.${value.article_suffix}` : value.article;
    const part = value.part ? ` ч. ${value.part}` : "";
    return `ст. ${article}${part} ${value.code || "УК РФ"}`;
  }
  if (fact.type === "offense_stage") return STAGE_LABELS[value] || value;
  const kind = FACT_VALUE_KINDS[fact.type];
  if (typeof kind === "object" && Object.prototype.hasOwnProperty.call(kind, value)) {
    return kind[value];
  }
  if (typeof value === "boolean") return value ? "да" : "нет";
  return String(value);
}

async function updateFact(analysisId, factId, body) {
  clearError();
  const response = await apiFetch(`/api/v1/analyses/${analysisId}/facts/${factId}`, {
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

  const originText =
    fact.status === "NOT_FOUND"
      ? "исключено из проверок"
      : fact.extraction_method === "user"
        ? "внесено судьёй"
        : fact.extraction_method === "llm"
          ? "извлечено моделью"
          : null;
  if (originText) {
    const origin = div("fact-origin");
    origin.textContent = originText;
    title.appendChild(origin);
  }

  const detail = div("detail hidden");
  const meta = div("meta");
  meta.appendChild(div("", `Статус: ${statusLabel(fact.status)}`));
  meta.appendChild(div("", `Метод извлечения: ${methodLabel(fact.extraction_method)}`));
  meta.appendChild(div("", `Уверенность: ${fact.confidence}`));
  if (fact.prompt_version) meta.appendChild(div("", `Промпт: ${fact.prompt_version}`));
  if (fact.rejection_reason) {
    meta.appendChild(div("", `Причина исключения: ${fact.rejection_reason}`));
  }

  fact.evidence.forEach((ev) => {
    const quote = document.createElement("blockquote");
    quote.textContent = ev.quote;
    detail.appendChild(quote);
    detail.appendChild(
      div("meta", `Фрагмент документа: символы ${ev.start_offset}–${ev.end_offset}` +
        (ev.page ? `, страница ${ev.page}` : ""))
    );
  });

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
      const reason = window.prompt(
        "Причина исключения факта из проверок (обязательна):"
      );
      if (!reason || !reason.trim()) return;
      updateFact(analysisId, fact.id, { status: "NOT_FOUND", reason: reason.trim() });
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

function renderEvaluation(ev, factsById, positions = []) {
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
      normsBlock.appendChild(div("step", `- ${norm.ref}${versionLabel}`));
    });
    detail.appendChild(normsBlock);
  }

  if (positions.length) {
    const practiceBlock = div("chain");
    practiceBlock.appendChild(div("step", "Разъяснения и практика Верховного Суда:"));
    positions.forEach((position) => {
      const status = position.verification_status === "verified" ? "сверено юристом" : "черновик, не сверен юристом";
      practiceBlock.appendChild(div("step", `- ${position.ref} (${status}): ${position.excerpt}`));
    });
    detail.appendChild(practiceBlock);
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
  const distEntries = Object.entries(analytics.punishment_type_distribution || {});
  const punishmentLabels = FACT_VALUE_KINDS.punishment_type;
  const distText = distEntries.length
    ? distEntries
        .map(([type, count]) => `${punishmentLabels[type] || type}: ${count}`)
        .join(", ")
    : "нет данных";
  dist.textContent = `Виды наказания: ${distText}`;

  const position = analytics.sentence_position;
  const positionBlock = div("sentence-position");
  if (position) {
    const share = Math.round(position.share_at_or_below * 100);
    positionBlock.appendChild(
      div(
        "summary-line",
        `${position.note} Срок не ниже, чем у ${share}% дел выборки ` +
          `(сравнивалось дел: ${position.n_compared}).`
      )
    );
    positionBlock.appendChild(
      div(
        "meta",
        "Выборка неслучайна и мала, обстоятельства дел различаются: " +
          "наблюдение не является основанием для изменения наказания."
      )
    );
  }

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
  if (position) wrap.appendChild(positionBlock);
  wrap.appendChild(table);
  wrap.appendChild(dist);
  if (Object.keys(analytics.feature_spread || {}).length) {
    wrap.appendChild(spreadTable);
  }
  wrap.appendChild(note);
}


const CONCERN_LABELS = {
  violation: "Проверка не сошлась",
  attention: "Требует внимания",
  data: "Ограничение данных",
  statistics: "Статистическое наблюдение",
};

const CONCERN_BADGE = {
  violation: "FAIL",
  attention: "WARNING",
  data: "UNKNOWN",
  statistics: "UNKNOWN",
};

function renderSummary(summary) {
  const panel = $("summary-panel");
  const list = $("summary-list");
  list.innerHTML = "";
  if (!summary || !summary.lines || !summary.lines.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("summary-headline").textContent = summary.headline || "";
  summary.lines.forEach((line) => {
    const li = document.createElement("li");
    li.appendChild(div("summary-line", line.text));
    if (line.fact_ids && line.fact_ids.length) {
      li.appendChild(div("meta", `обстоятельств в основе: ${line.fact_ids.length}`));
    }
    list.appendChild(li);
  });
}

function renderConcerns(concerns) {
  const panel = $("concerns-panel");
  const list = $("concerns-list");
  list.innerHTML = "";
  panel.hidden = false;
  if (!concerns.length) {
    list.appendChild(
      div("hint", "Проверки прошли, данных хватило: отдельных замечаний нет.")
    );
    return;
  }
  concerns.forEach((concern) => {
    const li = document.createElement("li");
    const title = div("fact-title");
    title.appendChild(badge(CONCERN_BADGE[concern.severity] || "UNKNOWN"));
    title.append(` ${CONCERN_LABELS[concern.severity] || concern.severity}: ${concern.title}`);
    li.appendChild(title);
    if (concern.detail) {
      if (concern.severity === "data" && concern.detail.includes("; ")) {
        const ul = document.createElement("ul");
        ul.className = "reasons";
        concern.detail.split("; ").forEach((item) => {
          const itemLi = document.createElement("li");
          itemLi.textContent = item;
          ul.appendChild(itemLi);
        });
        li.appendChild(ul);
      } else {
        li.appendChild(div("meta", concern.detail));
      }
    }
    if (concern.rule_id) li.appendChild(div("meta", `правило ${concern.rule_id}`));
    list.appendChild(li);
  });
}

function renderReport(report) {
  $("results").hidden = false;
  $("download-json").dataset.analysisId = report.analysis_id;

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

  renderSummary(report.summary);
  renderConcerns(report.concerns || []);
  const sourcesList = $("norm-sources");
  sourcesList.innerHTML = "";
  (report.norm_sources || []).forEach((source) => sourcesList.appendChild(renderNormSource(source)));

  const factsList = $("facts-list");
  factsList.innerHTML = "";
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
  const positionsByRule = {};
  (report.practice_positions || []).forEach((position) => {
    (positionsByRule[position.rule_id] = positionsByRule[position.rule_id] || []).push(position);
  });
  report.evaluations.forEach((ev) =>
    evalsList.appendChild(renderEvaluation(ev, factsById, positionsByRule[ev.rule_id] || []))
  );

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
  clearFactError();
  const box = $("add-fact-box");
  const type = $("new-fact-type").value;
  const value = newValueAccessor();
  const quote = $("new-fact-quote").value.trim();
  if (value === null || value === undefined || value === "") {
    showFactError("Заполните значение обстоятельства.");
    return;
  }
  if (!quote) {
    showFactError("Вставьте точную цитату из документа.");
    return;
  }
  const response = await apiFetch(`/api/v1/analyses/${box.dataset.analysisId}/facts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, value, quote }),
  });
  if (!response.ok) {
    showFactError(`Ошибка добавления: ${(await response.json()).detail || response.status}`);
    return;
  }
  $("new-fact-quote").value = "";
  renderReport(await response.json());
}

async function analyzeDocument(docId) {
  const body = {};
  const applicableAt = $("applicable-at").value;
  if (applicableAt) body.applicable_at = applicableAt;
  const response = await apiFetch(`/api/v1/documents/${docId}/analyze`, {
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
      const response = await apiFetch("/api/v1/documents", {
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
      const response = await apiFetch("/api/v1/documents/upload", { method: "POST", body: form });
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
  badgeEl.setAttribute(
    "aria-label",
    `лексическая релевантность ${hit.score.toFixed(2)} (справочная)`
  );
  title.appendChild(badgeEl);
  title.append(` ${hit.ref} — ${hit.title}`);

  const detail = div("detail hidden");
  const quote = document.createElement("blockquote");
  quote.textContent = hit.fragment;
  detail.appendChild(quote);

  const meta = div("meta");
  const period = hit.effective_to
    ? `${hit.effective_from} — ${hit.effective_to}`
    : `с ${hit.effective_from} (открытый интервал корпуса)`;
  meta.appendChild(div("", `Редакция: ${hit.version_id}; период действия: ${period}`));
  meta.appendChild(div("", `Применимость: ${hit.applicability || "не проверена"}; актуальность: ${hit.freshness || "не проверена"}`));
  meta.appendChild(div("", `Источник: ${hit.source_document}`));
  if (hit.source_url) meta.appendChild(div("", `URL: ${hit.source_url}`));
  meta.appendChild(div("", `Извлечено: ${hit.retrieved_at}; SHA-256: ${hit.sha256.slice(0, 16)}…`));
  meta.appendChild(div("", `Статус сверки: ${verificationLabel(hit.verification_status)}` +
    (hit.synthetic ? "; СИНТЕТИЧЕСКАЯ фикстура" : "")));
  meta.appendChild(div("", `Совпавшие термины: ${hit.matched_terms.join(", ")}`));
  detail.appendChild(meta);

  wireDisclosure(title, detail);
  li.appendChild(title);
  li.appendChild(detail);
  return li;
}

function renderNormSource(source) {
  const li = document.createElement("li");
  li.appendChild(div("", `${source.ref}: применимость ${source.applicability}; актуальность ${source.freshness}; получение ${source.acquisition}`));
  li.appendChild(div("hint", source.reasons.join(" ")));
  [source.local_fragment, source.candidate].filter(Boolean).forEach((snapshot) => {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${snapshot.verification_status === "candidate" ? "Кандидат для сверки" : "Сохранённая норма"}: ${snapshot.provider}, документ ${snapshot.document_id}, редакция ${snapshot.edition_id}`;
    const quote = document.createElement("blockquote");
    quote.textContent = snapshot.text;
    details.append(summary, quote,
      div("meta", `URL: ${snapshot.source_url}; получено ${snapshot.retrieved_at}; последняя проверка ${snapshot.last_checked_at || "не выполнена"}; SHA-256 ${snapshot.sha256}`));
    li.appendChild(details);
  });
  return li;
}

async function runSearch() {
  const box = $("search-error");
  box.hidden = true;
  const query = $("search-query").value.trim();
  const list = $("search-results");
  list.innerHTML = "";
  const article = $("search-article").value.trim();
  if (!query && !article) return;
  try {
    const params = new URLSearchParams({q: query, limit: "10"});
    if (article) {
      if (!/^\d+(\.\d+)?$/.test(article)) throw new Error("Укажите номер статьи, например 228.1");
      const [number, suffix] = article.split(".");
      params.set("code", "УК РФ");
      params.set("article", number);
      if (suffix) params.set("article_suffix", suffix);
      if ($("search-part").value) params.set("part", $("search-part").value);
      params.set("applicable_at", $("search-date").value);
    }
    const response = await apiFetch(
      `/api/v1/search?${params}`
    );
    if (!response.ok) {
      throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    }
    const body = await response.json();
    if (body.source_state) list.appendChild(renderNormSource(body.source_state));
    if (!body.results.length && !body.source_state) {
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

function normEditMessage(box, text, isError) {
  box.textContent = text;
  box.hidden = false;
  box.className = isError ? "error" : "hint";
  box.setAttribute("role", isError ? "alert" : "status");
}

async function submitNormEdit(url, body, button, messageBox) {
  messageBox.hidden = true;
  button.disabled = true;
  try {
    const response = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    normEditMessage(messageBox, "Готово: " + payload.message, false);
    setTimeout(loadCorpusState, 1200);
  } catch (error) {
    normEditMessage(messageBox, error.message, true);
    button.disabled = false;
  }
}

function normEditBox(norm) {
  const details = document.createElement("details");
  details.className = "norm-edit";

  const summary = document.createElement("summary");
  summary.textContent = "Свериться с источником";
  details.appendChild(summary);

  const messageBox = document.createElement("div");
  messageBox.hidden = true;
  details.appendChild(messageBox);

  const confirmRow = div("controls");
  const checkedInput = document.createElement("input");
  checkedInput.type = "date";
  checkedInput.value = new Date().toISOString().slice(0, 10);
  checkedInput.setAttribute("aria-label", "Дата, по которую сверена история");
  const sourceInput = document.createElement("input");
  sourceInput.type = "text";
  sourceInput.placeholder = "Источник сверки (например, КонсультантПлюс)";
  sourceInput.size = 28;
  sourceInput.setAttribute("aria-label", "Источник сверки");
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "secondary";
  confirmBtn.textContent = "Статья не менялась — подтвердить";
  confirmRow.append(checkedInput, sourceInput, confirmBtn);
  details.appendChild(div("hint", "Сверка истории изменений: подтверждает, что единственная редакция — факт, а не допущение."));
  details.appendChild(confirmRow);

  confirmBtn.addEventListener("click", () => {
    if (!sourceInput.value.trim()) {
      normEditMessage(messageBox, "Укажите источник сверки.", true);
      return;
    }
    submitNormEdit(
      `/api/v1/norms/${encodeURIComponent(norm.norm_id)}/confirm-history`,
      { checked_through: checkedInput.value, source: sourceInput.value.trim() },
      confirmBtn,
      messageBox
    );
  });

  const versionText = document.createElement("textarea");
  versionText.rows = 3;
  versionText.placeholder = "Текст новой редакции из официального источника";
  versionText.setAttribute("aria-label", "Текст новой редакции");
  const fromRow = div("controls");
  const fromLabel = document.createElement("label");
  fromLabel.textContent = "Действует с:";
  fromLabel.className = "visually-hidden";
  fromLabel.setAttribute("for", `nv-from-${norm.norm_id}`);
  const fromInput = document.createElement("input");
  fromInput.type = "date";
  fromInput.id = `nv-from-${norm.norm_id}`;
  fromInput.setAttribute("aria-label", "Действует с");
  const sourceUrlInput = document.createElement("input");
  sourceUrlInput.type = "text";
  sourceUrlInput.placeholder = "Ссылка на источник (необязательно)";
  sourceUrlInput.size = 28;
  sourceUrlInput.setAttribute("aria-label", "Ссылка на источник");
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "secondary";
  addBtn.textContent = "Добавить редакцию";
  fromRow.append(fromLabel, fromInput, sourceUrlInput, addBtn);
  details.appendChild(
    div("hint", "Новая редакция: закрывает действующий интервал и приходит статусом «черновик» — пределы наказания вносятся отдельно.")
  );
  const versionRow = div("controls");
  versionRow.appendChild(versionText);
  details.appendChild(versionRow);
  details.appendChild(fromRow);

  addBtn.addEventListener("click", () => {
    if (!versionText.value.trim()) {
      normEditMessage(messageBox, "Вставьте текст редакции.", true);
      return;
    }
    if (!fromInput.value) {
      normEditMessage(messageBox, "Укажите дату, с которой действует редакция.", true);
      return;
    }
    submitNormEdit(
      `/api/v1/norms/${encodeURIComponent(norm.norm_id)}/add-version`,
      {
        text: versionText.value.trim(),
        effective_from: fromInput.value,
        source_url: sourceUrlInput.value.trim(),
      },
      addBtn,
      messageBox
    );
  });

  return details;
}

function corpusRow(norm) {
  const li = document.createElement("li");
  const head = div("fact-title");
  head.appendChild(badge(norm.needs_review ? "WARNING" : "PASS"));
  head.appendChild(document.createTextNode(` ${norm.ref}`));
  li.appendChild(head);

  const to = norm.covered_to || "н/в";
  const parts = [
    `редакций: ${norm.version_count}`,
    `покрытие: ${norm.covered_from} -> ${to}`,
    norm.draft_rule_only
      ? "упомянута в черновике правила, не активна"
      : norm.rule_relevant
        ? "читается движком правил"
        : "справочная",
  ];
  if (norm.history_verified) parts.push("история сверена юристом");
  li.appendChild(div("detail", parts.join("; ")));

  if (norm.gaps.length) {
    li.appendChild(
      div("detail", `Разрывы в покрытии: ${norm.gaps.map((g) => `${g[0]} … ${g[1]}`).join(", ")}`)
    );
  }
  if (norm.history_unrecorded) {
    li.appendChild(
      div("detail", "Одна редакция на длинный интервал: утверждение «норма не менялась» не подтверждено сверкой.")
    );
  }
  li.appendChild(
    div("detail", norm.source_editions.length
      ? `Редакция источника: ${norm.source_editions.join(", ")}`
      : "Редакция источника не записана — устаревание не отследить.")
  );
  if (accountRole === "admin") li.appendChild(normEditBox(norm));
  return li;
}

function renderCorpusState(data) {
  const summary = $("corpus-summary");
  summary.textContent = "";
  summary.appendChild(div("", `Норм в корпусе: ${data.total}`));
  summary.appendChild(div("", `Требуют юридической сверки: ${data.needs_review}`));
  summary.appendChild(
    div("", `Целостность корпуса: ${
      data.corpus_sealed === true ? "опечатан, расхождений нет"
        : data.corpus_sealed === false ? "РАСХОЖДЕНИЕ с манифестом"
        : "не проверяется (носитель не файловый)"
    }`)
  );
  summary.hidden = false;

  const list = $("corpus-list");
  list.textContent = "";
  const ordered = [...data.norms].sort(
    (a, b) => Number(b.needs_review) - Number(a.needs_review) || a.ref.localeCompare(b.ref, "ru")
  );
  ordered.forEach((norm) => list.appendChild(corpusRow(norm)));
  list.hidden = false;
  $("corpus-freshness").hidden = false;
}

async function loadCorpusState() {
  const errorBox = $("corpus-error");
  errorBox.hidden = true;
  try {
    const response = await apiFetch("/api/v1/norms/coverage");
    if (!response.ok) throw new Error(`сервер ответил ${response.status}`);
    renderCorpusState(await response.json());
  } catch (error) {
    errorBox.textContent = `Не удалось получить состояние корпуса: ${error.message}`;
    errorBox.hidden = false;
  }
}

async function printReport() {
  const analysisId = $("download-json").dataset.analysisId;
  if (!analysisId) return;
  clearError();
  const mask = $("mask-personal").checked ? "?mask=true" : "";
  const response = await apiFetch(`/api/v1/analyses/${analysisId}/print${mask}`);
  if (!response.ok) {
    showError(`Не удалось подготовить печатную форму: HTTP ${response.status}`);
    return;
  }
  const frame = document.createElement("iframe");
  frame.className = "print-frame";
  frame.setAttribute("title", "Печатная форма заключения");
  frame.srcdoc = await response.text();
  frame.addEventListener("load", () => {
    frame.contentWindow.focus();
    frame.contentWindow.print();
  });
  document.body.appendChild(frame);
  window.setTimeout(() => frame.remove(), 120000);
}

$("print-btn").addEventListener("click", printReport);

$("download-docx").addEventListener("click", async (event) => {
  const analysisId = $("download-json").dataset.analysisId;
  if (!analysisId) return;
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const mask = $("mask-personal").checked ? "?mask=true" : "";
    const response = await apiFetch(`/api/v1/analyses/${analysisId}/document${mask}`);
    if (!response.ok) {
      showError(`Не удалось собрать заключение: HTTP ${response.status}`);
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download =
      `Заключение_${analysisId.slice(0, 8)}${mask ? "_обезличено" : ""}.docx`;
    link.click();
    URL.revokeObjectURL(url);
  } finally {
    button.disabled = false;
  }
});

$("download-json").addEventListener("click", async (event) => {
  event.preventDefault();
  const analysisId = event.currentTarget.dataset.analysisId;
  if (!analysisId) return;
  clearError();
  const response = await apiFetch(`/api/v1/analyses/${analysisId}`);
  if (!response.ok) {
    showError(`Не удалось выгрузить отчёт: HTTP ${response.status}`);
    return;
  }
  const blob = new Blob([await response.text()], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "second-opinion-report.json";
  link.click();
  URL.revokeObjectURL(url);
});
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
  const chosen = e.target.files.length > 0;
  $("file-name").textContent = chosen ? e.target.files[0].name : "Выбрать файл";
  $("file-label").classList.toggle("has-file", chosen);
});
$("search-btn").addEventListener("click", runSearch);
$("search-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});
$("corpus-btn").addEventListener("click", loadCorpusState);

const ROLE_LABELS = {
  judge: "Судья",
  clerk: "Помощник судьи",
  admin: "Администратор",
};

function showLoginError(message) {
  const box = $("login-error");
  box.textContent = message;
  box.hidden = false;
}

let accountRole = null;

function setAccountView(user) {
  const authorized = Boolean(user);
  const previousRole = accountRole;
  accountRole = authorized ? user.role : null;
  if (previousRole !== accountRole && !$("corpus-list").hidden) loadCorpusState();
  $("account-login").hidden = authorized;
  $("account-profile").hidden = !authorized;
  if (!authorized) return;

  const card = $("profile-card");
  card.innerHTML = "";
  [
    ["Пользователь", user.full_name || "—"],
    ["Электронная почта", user.email],
    ["Роль", ROLE_LABELS[user.role] || user.role],
  ].forEach(([label, value]) => {
    const row = div("kv");
    row.appendChild(div("k", label));
    row.appendChild(div("v", value));
    card.appendChild(row);
  });
}

async function loadProfile() {
  sessionStorage.removeItem("so-refresh");
  if (!sessionStorage.getItem("so-access") && hasSessionHint()) {
    if (!(await refreshSession())) setSessionHint(false);
  }
  if (!sessionStorage.getItem("so-access")) {
    setAccountView(null);
    return;
  }
  const response = await apiFetch("/auth/me");
  if (!response.ok) {
    sessionStorage.removeItem("so-access");
    setAccountView(null);
    return;
  }
  const profile = await response.json();
  setAccountView(profile);
  await loadHistory();
}

async function deleteAnalysis(analysisId) {
  const response = await apiFetch(`/api/v1/analyses/${analysisId}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${response.status}`);
  }
}

function historyRow(item) {
  const li = document.createElement("li");
  const main = div("history-row-main");
  const title = div("fact-title");

  const checks = item.checks || {};
  const worst = checks.FAIL
    ? "FAIL"
    : checks.WARNING
      ? "WARNING"
      : checks.UNKNOWN
        ? "UNKNOWN"
        : checks.PASS || !checks.NOT_APPLICABLE
          ? "PASS"
          : "NOT_APPLICABLE";
  title.appendChild(badge(worst));

  const when = (item.created_at || "").replace("T", " ").replace("Z", "");
  const parts = [item.qualification || "квалификация не извлечена", item.filename, when];
  const caseLabel = parts.filter(Boolean).join(" · ");
  title.append(` ${caseLabel}`);
  main.appendChild(title);

  const meta = div("meta");
  const COUNT_LABELS = {
    FAIL: "нарушений",
    WARNING: "требуют внимания",
    UNKNOWN: "без данных",
    PASS: "пройдено",
    NOT_APPLICABLE: "не применимо",
  };
  const counts = ["FAIL", "WARNING", "UNKNOWN", "PASS", "NOT_APPLICABLE"]
    .filter((key) => checks[key])
    .map((key) => `${COUNT_LABELS[key]} ${checks[key]}`)
    .join(", ");
  meta.textContent =
    `обстоятельств: ${item.facts_total}` +
    (counts ? `; проверки — ${counts}` : "") +
    (item.applicable_at ? `; нормы на ${item.applicable_at}` : "");
  main.appendChild(meta);
  li.appendChild(main);

  title.setAttribute("role", "button");
  title.setAttribute("tabindex", "0");
  const open = async () => {
    const response = await apiFetch(`/api/v1/analyses/${item.analysis_id}`);
    if (!response.ok) {
      showLoginError(`Не удалось открыть дело: HTTP ${response.status}`);
      return;
    }
    renderReport(await response.json());
    $("results").scrollIntoView({ behavior: "smooth" });
  };
  title.addEventListener("click", open);
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  });

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "history-delete";
  deleteBtn.textContent = "Удалить";
  deleteBtn.setAttribute("aria-label", `Удалить из истории: ${caseLabel}`);
  deleteBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm(`Удалить дело «${caseLabel}» из истории? Действие необратимо.`)) return;
    clearHistoryError();
    deleteBtn.disabled = true;
    try {
      await deleteAnalysis(item.analysis_id);
      await loadHistory();
    } catch (error) {
      showHistoryError(`Не удалось удалить дело: ${error.message}`);
      deleteBtn.disabled = false;
    }
  });
  li.appendChild(deleteBtn);

  return li;
}

async function clearHistory() {
  clearHistoryError();
  if (!confirm("Удалить всю историю дел этой учётной записи безвозвратно?")) return;
  const btn = $("history-clear-btn");
  btn.disabled = true;
  try {
    const response = await apiFetch("/api/v1/analyses?limit=1000");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    for (const item of body.items) {
      await deleteAnalysis(item.analysis_id);
    }
    await loadHistory();
  } catch (error) {
    showHistoryError(`Не удалось очистить историю: ${error.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function loadHistory() {
  const list = $("history-list");
  list.innerHTML = "";
  const response = await apiFetch("/api/v1/analyses?limit=20");
  if (!response.ok) {
    list.appendChild(div("hint", `История недоступна: HTTP ${response.status}`));
    return;
  }
  const body = await response.json();
  if (!body.items.length) {
    list.appendChild(div("hint", "Дел пока нет. Проанализируйте документ — он появится здесь."));
    return;
  }
  body.items.forEach((item) => list.appendChild(historyRow(item)));
}

async function login() {
  $("login-error").hidden = true;
  const email = $("login-email").value.trim();
  const password = $("login-password").value;
  if (!email || !password) {
    showLoginError("Введите почту и пароль.");
    return;
  }
  const response = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, cookie: true }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail;
    showLoginError(detail || `Вход не выполнен: HTTP ${response.status}`);
    return;
  }
  const body = await response.json();
  sessionStorage.setItem("so-access", body.tokens.access_token);
  setSessionHint(true);
  $("login-password").value = "";
  $("auth-box").hidden = true;
  await loadProfile();
}

async function logout() {
  if (sessionStorage.getItem("so-access") || hasSessionHint()) {
    await apiFetch("/auth/logout", { method: "POST" }).catch(() => {});
  }
  sessionStorage.removeItem("so-access");
  setSessionHint(false);
  $("history-list").innerHTML = "";
  setAccountView(null);
}

$("login-btn").addEventListener("click", login);
$("login-password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") login();
});
$("login-email").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("login-password").focus();
});
$("history-btn").addEventListener("click", loadHistory);
$("history-clear-btn").addEventListener("click", clearHistory);
$("logout-btn").addEventListener("click", logout);

loadAuthMode().then(loadProfile);
