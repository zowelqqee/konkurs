"""Отчёты, сводные таблицы и экспорт результатов мультивузового анализа."""

from __future__ import annotations

import io
import json
from typing import Any, Optional

import pandas as pd

from .allocation import applicant_status, result_preferences
from .cross_university import ambiguous_codes_for_university, estimate_release_scores, PASSING_STATUS
from .discovery import DiscoveryResult
from .models import (
    ApplicantKey,
    ApplicantProfile,
    CompetitionCategory,
    CrossUniversityResult,
    DataWarning,
    IntraUniversityResult,
    ProgramConfig,
    ProgramMetadata,
    ScenarioConfig,
    Status,
)


def _bool_ru(value: Optional[bool]) -> str:
    if value is True:
        return "да"
    if value is False:
        return "нет"
    return ""


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


def files_table(discovery: DiscoveryResult) -> pd.DataFrame:
    rows = []
    for record in discovery.files:
        rows.append(
            {
                "Файл": record.path.name,
                "Вуз": record.university_name or "",
                "Формат распознан": _bool_ru(record.format_ok),
                "Confidence": round(record.confidence, 2),
                "Программа": record.program_name or "",
                "Дата обновления": record.updated_at.isoformat() if record.updated_at else "",
                "Строк": record.row_count,
                "Отброшен": _bool_ru(record.dropped),
                "Причина": record.drop_reason,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Programs
# --------------------------------------------------------------------------


def programs_table(
    programs: dict[str, ProgramMetadata],
    configs: dict[str, ProgramConfig],
    intra_results: dict[str, IntraUniversityResult],
    cross_results: Optional[dict[ApplicantKey, CrossUniversityResult]] = None,
) -> pd.DataFrame:
    rows = []
    ambiguous_by_uni: dict[str, set[str]] = {}
    if cross_results:
        for uni in {p.university_code for p in programs.values()}:
            ambiguous_by_uni[uni] = ambiguous_codes_for_university(cross_results, uni)

    for program_id, metadata in sorted(programs.items()):
        result = intra_results.get(metadata.university_code)
        pr = result.programs.get(program_id) if result else None
        config = configs.get(program_id, ProgramConfig())

        ambiguous_here = 0
        optimistic = conservative = None
        freed = 0
        if result and pr:
            codes = ambiguous_by_uni.get(metadata.university_code, set())
            ambiguous_here = sum(
                1 for s in pr.allocated if not s.bvi and s.application.code in codes
            )
            optimistic, conservative, freed = estimate_release_scores(program_id, result, codes)

        rows.append(
            {
                "Вуз": metadata.university_name,
                "Программа": metadata.title,
                "Всего мест": config.total_places,
                "БВИ": pr.bvi_count if pr else 0,
                "Особая квота": pr.quota_filled.get(CompetitionCategory.SPECIAL_QUOTA.value, 0) if pr else 0,
                "Отдельная квота": pr.quota_filled.get(CompetitionCategory.SEPARATE_QUOTA.value, 0) if pr else 0,
                "Целевая квота": pr.quota_filled.get(CompetitionCategory.TARGET_QUOTA.value, 0) if pr else 0,
                "Мест общего конкурса": pr.general_seats if pr else 0,
                "Распределено": pr.allocated_count if pr else 0,
                "Свободных мест": pr.free_seats if pr else 0,
                "Проходной балл": pr.passing_score if pr else None,
                "Последний проходящий": pr.last_admitted_id if pr else "",
                "Первый непроходящий": pr.first_rejected_id if pr else "",
                "Заявлений": pr.applications_count if pr else 0,
                "Уникальных абитуриентов": pr.unique_applicants if pr else 0,
                "Ушли на выше приоритет (внутри вуза)": pr.left_for_higher if pr else 0,
                "Одновременно проходят в другом вузе": ambiguous_here,
                "Освободится мест (оптимистично)": freed,
                "Проходной консервативный": conservative,
                "Проходной оптимистический": optimistic,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Applicants
# --------------------------------------------------------------------------


def applicants_table(profiles: dict[ApplicantKey, ApplicantProfile]) -> pd.DataFrame:
    rows = []
    for key, profile in sorted(profiles.items(), key=lambda kv: kv[0].applicant_id):
        rows.append(
            {
                "Код": key.applicant_id,
                "Год кампании": key.campaign_year,
                "Вузы": ", ".join(sorted(profile.universities)),
                "Заявлений всего": len(profile.applications),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Intra-university allocation / Cross-university offers / Selected offers
# --------------------------------------------------------------------------


def intra_allocation_table(
    intra_results: dict[str, IntraUniversityResult], programs: dict[str, ProgramMetadata]
) -> pd.DataFrame:
    rows = []
    for university_code, result in sorted(intra_results.items()):
        for program_id, pr in sorted(result.programs.items()):
            for seat in pr.allocated:
                rows.append(
                    {
                        "Вуз": university_code,
                        "Код": seat.application.code,
                        "Программа": programs[program_id].title,
                        "Категория": "БВИ" if seat.bvi else seat.category.value,
                        "Приоритет": seat.priority,
                        "Место в конкурсе": seat.rank,
                        "Балл": seat.application.total_score,
                        "Согласие": _bool_ru(seat.application.consent),
                    }
                )
    return pd.DataFrame(rows)


def cross_offers_table(cross_results: dict[ApplicantKey, CrossUniversityResult]) -> pd.DataFrame:
    rows = []
    for key, cross in sorted(cross_results.items(), key=lambda kv: kv[0].applicant_id):
        for offer in cross.offers:
            rows.append(
                {
                    "Код": key.applicant_id,
                    "Вуз": offer.university_code,
                    "Программа": offer.program_id or "",
                    "Приоритет": offer.priority,
                    "Статус": offer.status,
                    "Согласие": _bool_ru(offer.consent),
                    "Балл": offer.total_score,
                }
            )
    return pd.DataFrame(rows)


def selected_offers_table(cross_results: dict[ApplicantKey, CrossUniversityResult]) -> pd.DataFrame:
    rows = []
    for key, cross in sorted(cross_results.items(), key=lambda kv: kv[0].applicant_id):
        selected = cross.selected_offer
        rows.append(
            {
                "Код": key.applicant_id,
                "Выбранный вуз": selected.university_code if selected else "",
                "Программа": selected.program_id if selected else "",
                "Неопределённо": _bool_ru(cross.ambiguous),
                "Причина": cross.selection_reason,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def configuration_table(
    programs: dict[str, ProgramMetadata], configs: dict[str, ProgramConfig]
) -> pd.DataFrame:
    rows = []
    for program_id, metadata in sorted(programs.items()):
        config = configs.get(program_id, ProgramConfig())
        rows.append(
            {
                "program_id": program_id,
                "Вуз": metadata.university_name,
                "Программа": metadata.title,
                "Всего мест": config.total_places,
                "Тип финансирования": config.funding_type,
                "Особая квота": config.special_quota_places,
                "Отдельная квота": config.separate_quota_places,
                "Целевая квота": config.target_quota_places,
                "БВИ внутри бюджета": _bool_ru(config.bvi_within_budget),
                "Возврат квот в общий конкурс": _bool_ru(config.redistribute_unfilled_quota),
                "Учитывать «На рассмотрении»": _bool_ru(config.include_under_review),
                "Только согласия": _bool_ru(config.consent_only),
                "Максимальный приоритет": config.max_priority,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# User report — подробный профиль абитуриента
# --------------------------------------------------------------------------


def applicant_report(
    applicant_key: ApplicantKey,
    profile: ApplicantProfile,
    programs: dict[str, ProgramMetadata],
    intra_results: dict[str, IntraUniversityResult],
    cross_result: Optional[CrossUniversityResult],
    scenario: ScenarioConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    by_program: dict[str, list] = {}
    for app in profile.applications:
        by_program.setdefault(app.program_id, []).append(app)

    for program_id in sorted(by_program):
        metadata = programs[program_id]
        result = intra_results.get(metadata.university_code)
        for app in by_program[program_id]:
            ranked = result.rankings.get((program_id, "bvi" if app.bvi else app.competition_category.value), []) if result else []
            position = next((i for i, a in enumerate(ranked, start=1) if a.code == app.code), None)
            pr = result.programs.get(program_id) if result else None
            capacity = _track_capacity(pr, app.competition_category, app.bvi) if pr else 0
            ahead = position - 1 if position else None
            status = (
                applicant_status(app.code, program_id, programs, {program_id: by_program[program_id]}, result, scenario)
                if result
                else Status.NO_DATA
            )
            solo = position <= capacity if position is not None and capacity else None
            margin = capacity - position if position is not None and capacity else None

            rows.append(
                {
                    "Вуз": metadata.university_name,
                    "Программа": metadata.title,
                    "Приоритет внутри вуза": app.priority,
                    "Категория": "БВИ" if app.bvi else app.competition_category.value,
                    "Балл": app.total_score,
                    "Согласие": _bool_ru(app.consent),
                    "Статус заявления": app.application_status or ("активно" if app.active else "неактивно"),
                    "Исходное место": app.source_rank,
                    "Расчётное место": position,
                    "Проходит без учёта других программ": _bool_ru(solo),
                    "Итог внутривузового распределения": status.value,
                    "Итог межвузовского сценария": (
                        cross_result.selection_reason if cross_result else ""
                    ),
                    "Человек передо мной": ahead,
                    "Мест в конкурсе": capacity,
                    "Проходной балл программы": pr.passing_score if pr else None,
                    "Запас / дефицит": margin,
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Вуз", "Приоритет внутри вуза"], na_position="last").reset_index(drop=True)
    return df


def _track_capacity(pr, category: CompetitionCategory, bvi: bool) -> int:
    if pr is None:
        return 0
    if bvi:
        return pr.bvi_count
    if category is CompetitionCategory.GENERAL:
        return pr.general_seats
    return pr.quota_filled.get(category.value, 0)


def explain_intra(
    applicant_key: ApplicantKey,
    program_id: str,
    programs: dict[str, ProgramMetadata],
    applications_by_program: dict[str, list],
    intra_results: dict[str, IntraUniversityResult],
    scenario: ScenarioConfig,
) -> str:
    """Человеческое объяснение внутривузового решения по одной программе."""
    metadata = programs.get(program_id)
    if metadata is None:
        return "Программа не найдена."
    result = intra_results.get(metadata.university_code)
    code = applicant_key.applicant_id
    apps = [a for a in applications_by_program.get(program_id, []) if a.code == code]
    if not apps or result is None:
        return f"Абитуриент {code} не подавал заявление на «{metadata.title}» ({metadata.university_name})."

    title = f"{metadata.title} ({metadata.university_name})"
    priority = min((a.priority for a in apps if a.priority is not None), default=None)
    target = result.assignment.get(applicant_key)
    prefs = result_preferences(result, code)

    if target == program_id:
        seat = next((s for s in result.programs[program_id].allocated if s.application.code == code), None)
        track = "БВИ" if (seat and seat.bvi) else (seat.category.value if seat else "общий конкурс")
        return (
            f"В {metadata.university_name} абитуриент {code} распределён на «{metadata.title}» "
            f"(приоритет {priority}, конкурс: {track})."
        )

    if target is not None and program_id in prefs and target in prefs:
        if prefs.index(target) < prefs.index(program_id):
            target_meta = programs[target]
            return (
                f"В {metadata.university_name} абитуриент не распределён на «{metadata.title}», "
                f"потому что внутри этого же вуза проходит на «{target_meta.title}» с более высоким приоритетом."
            )

    higher = [p for p in prefs if prefs.index(p) < prefs.index(program_id)] if program_id in prefs else []
    if higher:
        names = ", ".join(programs[p].title for p in higher if programs[p].university_code == metadata.university_code)
        if names:
            return (
                f"В {metadata.university_name} абитуриент рассматривается на «{metadata.title}» "
                f"с приоритетом {priority}, поскольку на программах с более высоким приоритетом "
                f"({names}) не вошёл в число проходящих."
            )
    return f"В {metadata.university_name} абитуриент {code} не проходит на «{metadata.title}»: не хватило баллов до границы."


def explain_cross(cross_result: Optional[CrossUniversityResult]) -> str:
    if cross_result is None:
        return "Межвузовский сценарий не рассчитан."
    passing = [o for o in cross_result.offers if o.status == PASSING_STATUS]
    if len(passing) <= 1:
        return cross_result.selection_reason
    universities = " и ".join(o.university_code.upper() for o in passing)
    if cross_result.ambiguous:
        return (
            f"Абитуриент одновременно проходит в {universities}. "
            f"Итоговый межвузовский выбор неизвестен: {cross_result.selection_reason}."
        )
    return (
        f"Абитуриент одновременно проходит в {universities}. "
        f"{cross_result.selection_reason}."
    )


# --------------------------------------------------------------------------
# Warnings
# --------------------------------------------------------------------------


def warnings_table(warnings: list[DataWarning]) -> pd.DataFrame:
    if not warnings:
        return pd.DataFrame(columns=["Тип", "Уровень", "Вуз", "Программа", "Код", "Сообщение"])
    return pd.DataFrame(
        [
            {
                "Тип": w.kind,
                "Уровень": w.severity,
                "Вуз": w.university_code or "",
                "Программа": w.program_id or "",
                "Код": w.applicant_id or "",
                "Сообщение": w.message,
            }
            for w in warnings
        ]
    )


# --------------------------------------------------------------------------
# Экспорт
# --------------------------------------------------------------------------


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV с BOM — чтобы Excel не ломал кириллицу."""
    return df.to_csv(index=False).encode("utf-8-sig")


def to_json_bytes(
    discovery: DiscoveryResult,
    configs: dict[str, ProgramConfig],
    intra_results: dict[str, IntraUniversityResult],
    cross_results: dict[ApplicantKey, CrossUniversityResult],
    warnings: list[DataWarning],
) -> bytes:
    payload = {
        "programs": [
            {
                "program_id": pid,
                "university": m.university_code,
                "name": m.program_name,
                "total_places": configs.get(pid, ProgramConfig()).total_places,
            }
            for pid, m in sorted(discovery.programs.items())
        ],
        "intra_university": {
            uni: {
                "iterations": r.iterations,
                "converged": r.converged,
                "assignment": {str(k): v for k, v in r.assignment.items()},
            }
            for uni, r in intra_results.items()
        },
        "cross_university": [
            {
                "code": key.applicant_id,
                "ambiguous": cross.ambiguous,
                "selected": (
                    {"university": cross.selected_offer.university_code, "program": cross.selected_offer.program_id}
                    if cross.selected_offer
                    else None
                ),
                "reason": cross.selection_reason,
                "offers": [
                    {"university": o.university_code, "program": o.program_id, "status": o.status}
                    for o in cross.offers
                ],
            }
            for key, cross in cross_results.items()
        ],
        "warnings": [w.to_dict() for w in warnings],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def to_excel_bytes(
    discovery: DiscoveryResult,
    configs: dict[str, ProgramConfig],
    intra_results: dict[str, IntraUniversityResult],
    profiles: dict[ApplicantKey, ApplicantProfile],
    cross_results: dict[ApplicantKey, CrossUniversityResult],
    warnings: list[DataWarning],
    scenario: ScenarioConfig,
    applicant_key: Optional[ApplicantKey] = None,
) -> bytes:
    """Excel с листами Files/Programs/Applicants/Intra-university allocation/
    Cross-university offers/Selected offers/User report/Warnings/Configuration."""
    buffer = io.BytesIO()

    user_df = pd.DataFrame()
    if applicant_key is not None and applicant_key in profiles:
        user_df = applicant_report(
            applicant_key,
            profiles[applicant_key],
            discovery.programs,
            intra_results,
            cross_results.get(applicant_key),
            scenario,
        )
    if user_df.empty:
        user_df = pd.DataFrame({"Сообщение": ["Код абитуриента не задан или не найден."]})

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        files_table(discovery).to_excel(writer, sheet_name="Files", index=False)
        programs_table(discovery.programs, configs, intra_results, cross_results).to_excel(
            writer, sheet_name="Programs", index=False
        )
        applicants_table(profiles).to_excel(writer, sheet_name="Applicants", index=False)
        intra_allocation_table(intra_results, discovery.programs).to_excel(
            writer, sheet_name="Intra-university allocation", index=False
        )
        cross_offers_table(cross_results).to_excel(writer, sheet_name="Cross-university offers", index=False)
        selected_offers_table(cross_results).to_excel(writer, sheet_name="Selected offers", index=False)
        user_df.to_excel(writer, sheet_name="User report", index=False)
        warnings_table(warnings).to_excel(writer, sheet_name="Warnings", index=False)
        configuration_table(discovery.programs, configs).to_excel(writer, sheet_name="Configuration", index=False)
    return buffer.getvalue()
