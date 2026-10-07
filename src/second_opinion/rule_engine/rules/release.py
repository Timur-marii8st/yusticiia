"""Глава 11 УК РФ: освобождение от уголовной ответственности (ст. 75, 76, 76², черновик).

Требование п. 2.5 Положения о конкурсе: прототип обязан учитывать
основания освобождения от уголовной ответственности и наказания по
разделу IV УК РФ. Написано 20.09.2026, **не зарегистрировано в
``DEFAULT_RULES``** (см. ``rule_engine/engine.py``) и поэтому не может
попасть в отчёт судье — правило без верифицированной юристом нормы-
основания добавлять запрещает инвариант И-10, а нормы главы 11 в корпусе
сейчас ``verification_status: draft`` (``data/fixtures/norms/
uk_rf_chapter11_draft.json`` — текст получен автоматически из ИПС, юрист
его ещё не смотрел).

Правило написано заранее, чтобы юрист сверял готовый код, а не ждал, пока
его напишут после сверки. Активация — одной строкой в ``DEFAULT_RULES``,
после того как нормы получат ``verification_status: verified``.

Почему одно правило на три статьи, а не три. Ст. 75 (деятельное
раскаяние), ст. 76 (примирение с потерпевшим) и ст. 76² (судебный штраф)
делят общую предпосылку — впервые совершённое преступление небольшой или
средней тяжести с загладенным вредом — и различаются условиями, которые
система не извлекает и не может извлечь из текста приговора: заявил ли
подсудимый ходатайство, согласен ли потерпевший на примирение, готов ли
суд применить штраф вместо ответственности. Разносить это на три
отдельных правила означало бы утроить один и тот же текст ради различия,
которое всё равно сводится к «решает суд, не система».

Почему только WARNING и UNKNOWN. Основание освобождения — не наказание, а
альтернатива ему: если правило скажет «применимо» увереннее, чем судья
сам решит, оно выйдет за п. 2.5.3 Положения (не заменять судью). Формула
результата — «проверьте, есть ли основание», никогда «есть основание» и
никогда «прекратите дело».

Чего это правило не покрывает — и почему до другого раза:

- ст. 76¹ (экономические преступления, ст. 198–199⁴ и другие) — вне
  статей, которые понимает движок правил;
- ст. 78 (сроки давности) — требует не только квалификации, но и даты
  вынесения приговора относительно даты деяния; в модели фактов есть
  ``DATE_OF_OFFENSE``, но не дата приговора — отдельная задача расширения
  модели, а не этого правила;
- различие «умышленное / по неосторожности» для категории преступления
  (ст. 15 УК РФ) не извлекается: все статьи, которые сейчас понимает
  движок (111, 112, 158, 159, 161, 163, 228), — умышленные, поэтому
  категория считается по одной только санкции. Расширение на
  неосторожные составы потребует сначала научиться извлекать форму вины.
"""

from __future__ import annotations

from ...domain.enums import FactType, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.norms import NormRef
from ...domain.rules import LegalRule, RuleEvaluation
from ...legal_sources.store import NoApplicableVersionError, NormNotFoundError
from .base import IMPRISONMENT, _facts_of_type, _versioned_ref

CATEGORY_LIMIT_MONTHS = 5 * 12

_SURRENDER_CODE = "61.1.и"
_RESTITUTION_CODE = "61.1.к"

_BASES = {
    "uk-rf:art-75": "ст. 75 УК РФ (деятельное раскаяние)",
    "uk-rf:art-76": "ст. 76 УК РФ (примирение с потерпевшим)",
    "uk-rf:art-76-2": "ст. 76.2 УК РФ (судебный штраф)",
}


class ReleaseFromResponsibilityRule(LegalRule):
    """Впервые совершённое преступление небольшой/средней тяжести с
    загладенным вредом — повод проверить главу 11 УК РФ."""

    rule_id = "R-D01"
    version = "0.1.0-draft"
    title = "Основания освобождения от уголовной ответственности (гл. 11 УК РФ)"
    description = (
        "Впервые совершённое преступление небольшой или средней тяжести, при "
        "котором заглажен вред или возмещён ущерб, может подпадать под "
        "ст. 75, 76 или 76.2 УК РФ. Правило указывает на признаки в "
        "извлечённых фактах, не устанавливает наличие основания."
    )
    norm_refs = ["uk-rf:art-75", "uk-rf:art-76", "uk-rf:art-76-2"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        qualification = case.primary_qualification
        if qualification is None:
            return self._unknown(
                ["квалификация (статья УК РФ)"],
                "Без квалификации нельзя определить категорию преступления — "
                "основа для проверки оснований главы 11 УК РФ.",
            )

        norm = context.norm_store.find_by_article(
            qualification.code,
            qualification.article,
            qualification.part,
            qualification.article_suffix,
        )
        if norm is None:
            return self._unknown(
                [f"норма для {qualification.ref} в базе источников не найдена"],
                "Категорию преступления определить не удалось: санкция статьи "
                "отсутствует в базе источников.",
            )
        try:
            version = context.norm_store.get_norm(norm.norm_id, context.applicable_at)
        except (NoApplicableVersionError, NormNotFoundError):
            return self._unknown(
                [f"редакция {norm.ref} на дату {context.applicable_at.isoformat()}"],
                "Не найдена редакция нормы, действовавшая на юридически значимую "
                "дату — категорию преступления определить не удалось.",
            )
        sanction = next(
            (s for s in version.sanctions if s.punishment_type.value == IMPRISONMENT),
            None,
        )
        if sanction is None or sanction.max_months is None:
            return self._unknown(
                [f"максимум лишения свободы по {norm.ref}"],
                "В данных санкции нет предела лишения свободы — категорию "
                "преступления определить не удалось.",
            )

        norm_ref = NormRef(norm_id=norm.norm_id, version_id=version.version_id, ref=norm.ref)
        quality_fact_ids = [f.id for f in _facts_of_type(case, FactType.QUALIFICATION)]

        if sanction.max_months > CATEGORY_LIMIT_MONTHS:
            return self._evaluation(
                status=RuleStatus.NOT_APPLICABLE,
                headline=f"{self.title}: не применимо",
                explanation=(
                    f"Максимум санкции по {norm.ref} — {sanction.max_months:.0f} мес., "
                    "преступление тяжкое или особо тяжкое (ч. 4, 5 ст. 15 УК РФ). "
                    "Ст. 75, 76, 76.2 применяются только к преступлениям небольшой "
                    "или средней тяжести."
                ),
                facts_used=quality_fact_ids,
                norms_used=[norm_ref],
                numbers={"max_months": sanction.max_months},
            )

        prior = case.defendant.prior_convictions
        if prior is None:
            return self._unknown(
                ["совершено ли преступление впервые (сведения о судимости)"],
                "Категория преступления (небольшой/средней тяжести) допускает "
                "главу 11 УК РФ, но не извлечено, совершено ли преступление "
                "впервые — обязательное условие ст. 75, 76, 76.2.",
            )
        if prior:
            return self._evaluation(
                status=RuleStatus.NOT_APPLICABLE,
                headline=f"{self.title}: не применимо",
                explanation=(
                    "Преступление совершено не впервые (сведения о судимости "
                    "извлечены) — ст. 75, 76, 76.2 УК РФ требуют, чтобы "
                    "преступление было совершено впервые."
                ),
                facts_used=quality_fact_ids
                + [f.id for f in _facts_of_type(case, FactType.PRIOR_CONVICTIONS)],
                norms_used=[norm_ref],
            )

        codes = case.mitigating_codes()
        surrender = _SURRENDER_CODE in codes
        restitution = _RESTITUTION_CODE in codes
        if not restitution:
            return self._unknown(
                ["возмещение ущерба или заглаживание вреда"],
                "Категория и отсутствие судимости допускают главу 11 УК РФ, но "
                "возмещение ущерба или заглаживание вреда не извлечено из "
                "текста — обязательное условие всех трёх статей. Это может "
                "означать, что вред не заглажен, а может — что документ "
                "не был явно проверен извлекателем; система не делает "
                "предположений."
            )

        applicable = ["uk-rf:art-76-2"] + (["uk-rf:art-75"] if surrender else [])
        candidates = "; ".join(_BASES[norm_id] for norm_id in applicable)
        mitigating_fact_ids = [
            fact_id
            for mitigating in case.mitigating
            if mitigating.code in {_SURRENDER_CODE, _RESTITUTION_CODE}
            for fact_id in mitigating.fact_ids
        ]
        return self._evaluation(
            status=RuleStatus.WARNING,
            headline=f"{self.title}: проверьте вручную",
            explanation=(
                "Преступление небольшой или средней тяжести, совершено впервые, "
                "вред заглажен или ущерб возмещён — проверьте, не может ли быть "
                f"применена {candidates}. Систему не проверяет: заявлено ли "
                "ходатайство, согласен ли потерпевший на примирение (ст. 76), "
                "готов ли суд применить судебный штраф вместо ответственности "
                "(ст. 76.2). Решение — исключительно за судом."
            ),
            facts_used=quality_fact_ids
            + [f.id for f in _facts_of_type(case, FactType.PRIOR_CONVICTIONS)]
            + mitigating_fact_ids,
            norms_used=[norm_ref]
            + [_versioned_ref(context, norm_id, _BASES[norm_id]) for norm_id in applicable],
        )
