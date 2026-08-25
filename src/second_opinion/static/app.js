"use strict";

const $ = (id) => document.getElementById(id);

const FACT_LABELS = {
  qualification: "Квалификация",
  defendant_age: "Возраст подсудимого",
  prior_convictions: "Наличие судимостей",
  minor_dependents: "Несовершеннолетние дети",
  health_factor: "Состояние здоровья",
  offense_stage: "Стадия преступления",
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
  return span;
}

function toggleDetail(detail) {
  detail.classList.toggle("hidden");
}

function div(className, text) {
  const el = document.createElement("div");
  el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
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

function renderFact(fact) {
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
  detail.appendChild(meta);

  title.addEventListener("click", () => toggleDetail(detail));
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

  title.addEventListener("click", () => toggleDetail(detail));
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

  title.addEventListener("click", () => toggleDetail(detail));
  li.appendChild(title);
  li.appendChild(detail);
  return li;
}

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
  const note = div("meta", "Статистика описательная и не является рекомендацией о наказании.");
  const wrap = $("analytics");
  wrap.innerHTML = "";
  wrap.appendChild(table);
  wrap.appendChild(dist);
  wrap.appendChild(note);
}

function renderReport(report) {
  $("results").hidden = false;

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
  const factsById = {};
  report.facts.forEach((fact) => {
    factsById[fact.id] = fact;
    factsList.appendChild(renderFact(fact));
  });
  if (!report.facts.length) {
    factsList.appendChild(div("hint", "Факты не извлечены."));
  }

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

async function analyzeDocument(docId) {
  const body = {};
  const applicableAt = $("applicable-at").value;
  if (applicableAt) body.applicable_at = applicableAt;
  const response = await fetch(`/api/documents/${docId}/analyze`, {
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
      const response = await fetch("/api/documents", {
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
      const response = await fetch("/api/documents/upload", { method: "POST", body: form });
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

$("analyze-btn").addEventListener("click", submit);
$("demo-btn").addEventListener("click", loadDemo);
$("doc-text").addEventListener("input", (e) => {
  documentText = e.target.value;
});
$("doc-file").addEventListener("change", (e) => {
  documentText = "";
  $("doc-text").value = "";
  $("file-name").textContent = e.target.files.length ? e.target.files[0].name : "или выберите файл…";
});
