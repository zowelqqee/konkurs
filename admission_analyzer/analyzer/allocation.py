"""Внутривузовское распределение: отложенное принятие (Гейл-Шепли).

Работает СТРОГО в рамках одного вуза: приоритеты разных вузов не сравнимы
(п. 5 ТЗ), поэтому `allocate_all` partition-ирует заявления по
`university_code` и запускает независимый расчёт для каждого вуза. Внутри
одного вуза алгоритм — тот же, что и в однoвузовой версии этого проекта:

1. Абитуриент предлагает себя программе с самым высоким приоритетом, откуда
   ещё не был отклонён.
2. Каждый конкурс программы (БВИ / квоты / общий) удерживает лучших по своему
   рейтингу и отклоняет остальных.
3. Отклонённые идут к следующему приоритету. Повторяется до стабилизации.

БВИ — это флаг `Application.bvi`, а не отдельная категория: БВИ-заявка любой
категории рассматривается в БВИ-конкурсе программы первой, ранжируется по
исходному месту в списке (`source_rank`), а не по баллам.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable, Optional

from .models import (
    Application,
    ApplicantKey,
    CATEGORY_TRACK_ORDER,
    CompetitionCategory,
    DataWarning,
    IntraUniversityResult,
    ProgramConfig,
    ProgramMetadata,
    ProgramResult,
    ScenarioConfig,
    Seat,
    Status,
)
from .normalization import header_key

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10_000

BVI_TRACK = "bvi"  # ключ трека БВИ внутри программы (не CompetitionCategory)
Track = tuple[str, str]  # (program_id, "bvi" | CompetitionCategory.value)

STATUS_UNDER_REVIEW_KEY = "на рассмотрении"


def _neg(value: Optional[int]) -> int:
    return -value if value is not None else 1


def general_sort_key(app: Application) -> tuple:
    """Ключ очерёдности общего конкурса и квот.

    Порядок (п. «Рейтинг внутри программы» ТЗ):
    1. сумма конкурсных баллов по убыванию;
    2. сумма вступительных испытаний по убыванию;
    3. индивидуальные достижения по убыванию;
    4. преимущественное право;
    5. исходное место в опубликованном списке;
    6. код абитуриента — ТЕХНИЧЕСКИЙ стабильный критерий, не официальное
       правило вуза (см. README).
    """
    return (
        _neg(app.total_score),
        _neg(app.exam_sum),
        _neg(app.individual_score),
        0 if app.preferential_right else 1,
        app.source_rank if app.source_rank is not None else 10**9,
        tie_breaker(app),
    )


def tie_breaker(app: Application) -> str:
    """Технический разрыв полного равенства. Не является правилом вуза."""
    return app.code


def bvi_sort_key(app: Application) -> tuple:
    """БВИ ранжируются по исходному месту в списке, не по баллам."""
    return (app.source_rank if app.source_rank is not None else 10**9, app.code)


def rank_applications(apps: Iterable[Application], bvi: bool) -> list[Application]:
    key = bvi_sort_key if bvi else general_sort_key
    return sorted(apps, key=key)


def _eligible(app: Application, scenario: ScenarioConfig) -> bool:
    """Участвует ли строка в распределении бюджетных мест этого сценария."""
    if not app.active:
        return False
    if not app.bvi and not app.competition_category.is_budget:
        return False  # платные места и неизвестная категория мест не занимают
    if app.priority is None:
        return False
    if scenario.max_priority is not None and app.priority > scenario.max_priority:
        return False
    if app.bvi and not scenario.count_bvi:
        return False
    if scenario.respect_consent and app.consent is not True:
        return False
    if app.application_status and header_key(app.application_status) == STATUS_UNDER_REVIEW_KEY:
        if not scenario.include_under_review:
            return False
    return True


def build_entries(
    applications: dict[str, list[Application]], scenario: ScenarioConfig
) -> dict[Track, list[Application]]:
    """Заявления по конкурсам программы с учётом сценария и дедупликацией.

    Если после фильтров у одного кода в одном конкурсе оказалось несколько
    строк — берётся лучшая по рейтингу (защита от дублей внутри программы).
    """
    raw: dict[Track, dict[str, Application]] = defaultdict(dict)
    for program_id, apps in applications.items():
        for app in apps:
            if not _eligible(app, scenario):
                continue
            if app.bvi:
                track: Track = (program_id, BVI_TRACK)
            else:
                category = app.competition_category
                if not scenario.use_quotas and category.is_quota:
                    category = CompetitionCategory.GENERAL
                track = (program_id, category.value)
            code = app.code
            current = raw[track].get(code)
            if current is None:
                raw[track][code] = app
            else:
                key_fn = bvi_sort_key if app.bvi else general_sort_key
                if key_fn(app) < key_fn(current):
                    raw[track][code] = app
    return {track: rank_applications(apps.values(), track[1] == BVI_TRACK) for track, apps in raw.items()}


def build_preferences(entries: dict[Track, list[Application]]) -> dict[str, list[str]]:
    """Код → список program_id по возрастанию приоритета (1 — самый желанный).

    Приоритет программы для человека = минимальный приоритет среди его строк в
    этой программе (в одной программе у него может быть и БВИ-, и обычная
    строка с разными приоритетами).
    """
    best: dict[str, dict[str, int]] = defaultdict(dict)
    for (program_id, _), apps in entries.items():
        for app in apps:
            if app.priority is None:
                continue
            current = best[app.code].get(program_id)
            if current is None or app.priority < current:
                best[app.code][program_id] = app.priority
    return {
        code: [p for p, _ in sorted(prefs.items(), key=lambda kv: (kv[1], kv[0]))]
        for code, prefs in best.items()
    }


def _quota_caps(config: ProgramConfig, scenario: ScenarioConfig) -> dict[CompetitionCategory, int]:
    if not scenario.use_quotas:
        return {c: 0 for c in (CompetitionCategory.SPECIAL_QUOTA, CompetitionCategory.SEPARATE_QUOTA, CompetitionCategory.TARGET_QUOTA)}
    return {
        CompetitionCategory.SPECIAL_QUOTA: max(config.special_quota_places, 0),
        CompetitionCategory.SEPARATE_QUOTA: max(config.separate_quota_places, 0),
        CompetitionCategory.TARGET_QUOTA: max(config.target_quota_places, 0),
    }


def program_choice(
    program_id: str,
    config: ProgramConfig,
    pool: set[str],
    entries: dict[Track, list[Application]],
    scenario: ScenarioConfig,
) -> tuple[dict[str, tuple[str, bool]], int]:
    """Кого программа берёт из пула кандидатов и сколько мест у общего конкурса.

    Разбирает конкурсы по очереди: БВИ -> квоты -> общий конкурс. Человек, уже
    занявший место в более сильном конкурсе, из последующих исключается — это
    решает задачу дублирования строк одного кода в разных конкурсах одной
    программы (квота + общий конкурс).

    Возвращает (код -> (ключ конкурса, БВИ?), число мест общего конкурса).
    """
    budget = max(config.total_places, 0)
    admitted: dict[str, tuple[str, bool]] = {}

    def take(track_key: str, is_bvi: bool, capacity: int) -> int:
        if capacity <= 0:
            return 0
        ranked = entries.get((program_id, track_key), [])
        taken = 0
        for app in ranked:
            if taken >= capacity:
                break
            if app.code in pool and app.code not in admitted:
                admitted[app.code] = (track_key, is_bvi)
                taken += 1
        return taken

    bvi_pool = len(entries.get((program_id, BVI_TRACK), []))
    if not scenario.count_bvi:
        bvi_cap = 0
    elif config.bvi_within_budget:
        caps = _quota_caps(config, scenario)
        bvi_cap = min(bvi_pool, max(budget - sum(caps.values()), 0))
    else:
        bvi_cap = bvi_pool
    bvi_taken = take(BVI_TRACK, True, bvi_cap)

    caps = _quota_caps(config, scenario)
    filled = {category: take(category.value, False, cap) for category, cap in caps.items()}

    general = budget - sum(caps.values())
    if config.bvi_within_budget and scenario.count_bvi:
        general -= bvi_taken
    if scenario.redistribute_unfilled_quota and scenario.use_quotas:
        for category, cap in caps.items():
            general += max(cap - filled[category], 0)
    general = max(general, 0)
    take(CompetitionCategory.GENERAL.value, False, general)

    return admitted, general


def _deferred_acceptance(
    programs: dict[str, ProgramConfig],
    entries: dict[Track, list[Application]],
    preferences: dict[str, list[str]],
    scenario: ScenarioConfig,
) -> tuple[dict[str, tuple[str, str, bool]], dict[str, int], int, bool]:
    """Ядро DA для одного вуза. Код -> (program_id, track_key, bvi)."""
    program_ids = {pid for pid, _ in entries}
    pools: dict[str, set[str]] = {pid: set() for pid in program_ids}
    next_choice: dict[str, int] = dict.fromkeys(preferences, 0)
    free: set[str] = {code for code, prefs in preferences.items() if prefs}

    admitted: dict[str, dict[str, tuple[str, bool]]] = {pid: {} for pid in program_ids}
    general_caps: dict[str, int] = dict.fromkeys(program_ids, 0)
    iterations = 0

    while free and iterations < MAX_ITERATIONS:
        iterations += 1
        touched: set[str] = set()

        for code in sorted(free):
            prefs = preferences[code]
            index = next_choice[code]
            if index >= len(prefs):
                continue
            program_id = prefs[index]
            next_choice[code] = index + 1
            if program_id not in pools:
                continue
            pools[program_id].add(code)
            touched.add(program_id)

        for program_id in sorted(touched):
            chosen, general_cap = program_choice(
                program_id, programs[program_id], pools[program_id], entries, scenario
            )
            admitted[program_id] = chosen
            general_caps[program_id] = general_cap
            pools[program_id] = set(chosen)

        holders = {code for chosen in admitted.values() for code in chosen}
        free = {
            code
            for code, prefs in preferences.items()
            if code not in holders and next_choice[code] < len(prefs)
        }

    converged = iterations < MAX_ITERATIONS
    if not converged:
        logger.error("DA не сошёлся за %s итераций — защита от зацикливания", MAX_ITERATIONS)
    else:
        logger.info("Внутривузовское распределение сошлось за %s итераций", iterations)

    assignment: dict[str, tuple[str, str, bool]] = {}
    for program_id, chosen in admitted.items():
        for code, (track_key, is_bvi) in chosen.items():
            assignment[code] = (program_id, track_key, is_bvi)
    return assignment, general_caps, iterations, converged


def allocate_university(
    university_code: str,
    programs: dict[str, ProgramMetadata],
    configs: dict[str, ProgramConfig],
    applications: dict[str, list[Application]],
    scenario: ScenarioConfig,
) -> IntraUniversityResult:
    """Полное внутривузовское распределение для одного вуза."""
    own_programs = {pid: p for pid, p in programs.items() if p.university_code == university_code}
    own_applications = {pid: applications.get(pid, []) for pid in own_programs}
    own_configs = {pid: configs.get(pid, ProgramConfig()) for pid in own_programs}

    entries = build_entries(own_applications, scenario)
    preferences = build_preferences(entries)
    assignment, general_caps, iterations, converged = _deferred_acceptance(
        own_configs, entries, preferences, scenario
    )

    result = IntraUniversityResult(
        university_code=university_code,
        assignment={
            _find_key(own_applications, code): pid for code, (pid, _, _) in assignment.items()
        },
        rankings=entries,
        iterations=iterations,
        converged=converged,
        scenario=scenario,
    )
    result.programs = _build_program_results(
        own_programs, own_configs, entries, assignment, general_caps, preferences
    )
    if not converged:
        result.warnings.append(
            DataWarning(
                kind="not_converged",
                severity="error",
                university_code=university_code,
                message="Внутривузовское распределение не сошлось за отведённое число раундов.",
            )
        )
    return result


def _find_key(applications: dict[str, list[Application]], code: str) -> ApplicantKey:
    for apps in applications.values():
        for app in apps:
            if app.code == code:
                return app.applicant_key
    # Не должно происходить: код взят из этих же applications.
    raise KeyError(code)


def allocate_all(
    programs: dict[str, ProgramMetadata],
    configs: dict[str, ProgramConfig],
    applications: dict[str, list[Application]],
    scenario: ScenarioConfig,
) -> dict[str, IntraUniversityResult]:
    """Запустить внутривузовское распределение независимо по каждому вузу."""
    universities = sorted({p.university_code for p in programs.values()})
    return {
        code: allocate_university(code, programs, configs, applications, scenario)
        for code in universities
    }


def _build_program_results(
    programs: dict[str, ProgramMetadata],
    configs: dict[str, ProgramConfig],
    entries: dict[Track, list[Application]],
    assignment: dict[str, tuple[str, str, bool]],
    general_caps: dict[str, int],
    preferences: dict[str, list[str]],
) -> dict[str, ProgramResult]:
    results: dict[str, ProgramResult] = {}
    for program_id, metadata in programs.items():
        admitted_here = {code: (t, bvi) for code, (pid, t, bvi) in assignment.items() if pid == program_id}

        seats: list[Seat] = []
        track_order = [(BVI_TRACK, True)] + [(c.value, False) for c in CATEGORY_TRACK_ORDER]
        for track_key, is_bvi in track_order:
            ranked = entries.get((program_id, track_key), [])
            position = 0
            for app in ranked:
                if admitted_here.get(app.code) == (track_key, is_bvi):
                    position += 1
                    seats.append(
                        Seat(
                            applicant_key=app.applicant_key,
                            program_id=program_id,
                            category=(
                                CompetitionCategory.GENERAL if is_bvi else CompetitionCategory(track_key)
                            ),
                            bvi=is_bvi,
                            priority=app.priority,
                            rank=position,
                            application=app,
                        )
                    )

        general_admitted = [s for s in seats if not s.bvi and s.category is CompetitionCategory.GENERAL]
        passing_score = None
        passing_score_no_individual = None
        last_id = None
        if general_admitted:
            last = general_admitted[-1].application
            passing_score = last.total_score
            passing_score_no_individual = last.exam_sum
            last_id = last.code

        general_ranked = entries.get((program_id, CompetitionCategory.GENERAL.value), [])
        first_rejected = None
        first_rejected_score = None
        for app in general_ranked:
            if admitted_here.get(app.code) is None and app.code not in assignment:
                first_rejected = app.code
                first_rejected_score = app.total_score
                break

        program_codes = {
            app.code for (pid, _), apps in entries.items() if pid == program_id for app in apps
        }
        left = 0
        for code in program_codes:
            target = assignment.get(code)
            if target is None or target[0] == program_id:
                continue
            prefs = preferences.get(code, [])
            if program_id in prefs and target[0] in prefs:
                if prefs.index(target[0]) < prefs.index(program_id):
                    left += 1

        quota_filled = {
            category.value: sum(1 for s in seats if not s.bvi and s.category is category)
            for category in (
                CompetitionCategory.SPECIAL_QUOTA,
                CompetitionCategory.SEPARATE_QUOTA,
                CompetitionCategory.TARGET_QUOTA,
            )
        }
        applications_count = sum(len(apps) for (pid, _), apps in entries.items() if pid == program_id)
        seats_total = configs.get(program_id, ProgramConfig()).total_places

        results[program_id] = ProgramResult(
            program_id=program_id,
            university_code=metadata.university_code,
            title=metadata.title,
            seats_total=seats_total,
            bvi_count=sum(1 for s in seats if s.bvi),
            quota_filled=quota_filled,
            general_seats=general_caps.get(program_id, 0),
            allocated=seats,
            passing_score=passing_score,
            passing_score_no_individual=passing_score_no_individual,
            last_admitted_id=last_id,
            first_rejected_id=first_rejected,
            first_rejected_score=first_rejected_score,
            applications_count=applications_count,
            unique_applicants=len(program_codes),
            left_for_higher=left,
            free_seats=max(seats_total - len(seats), 0),
        )
    return results


def applicant_status(
    code: str,
    program_id: str,
    programs: dict[str, ProgramMetadata],
    applications: dict[str, list[Application]],
    result: IntraUniversityResult,
    scenario: ScenarioConfig,
) -> Status:
    """Статус конкретного абитуриента на конкретной программе внутри вуза."""
    if program_id not in programs:
        return Status.NO_DATA
    rows = [a for a in applications.get(program_id, []) if a.code == code]
    if not rows:
        return Status.NO_DATA

    target = result.assignment.get(_key_for(rows))
    if target == program_id:
        seat = next((s for s in result.programs[program_id].allocated if s.application.code == code), None)
        if seat is None:
            return Status.ALLOCATED
        if seat.bvi:
            return Status.BVI
        if seat.category.is_quota:
            return Status.QUOTA
        return Status.ALLOCATED

    if not any(a.active for a in rows):
        return Status.INACTIVE

    in_play = [a for a in rows if _eligible(a, scenario)]
    if not in_play:
        if scenario.respect_consent and all(a.consent is not True for a in rows):
            return Status.NO_CONSENT
        if all(a.priority is None for a in rows):
            return Status.NO_DATA
        return Status.NOT_ALLOCATED

    if target is not None:
        prefs = result_preferences(result, code)
        if program_id in prefs and target in prefs:
            if prefs.index(target) < prefs.index(program_id):
                return Status.ALLOCATED_HIGHER
    return Status.NOT_ALLOCATED


def _key_for(rows: list[Application]) -> ApplicantKey:
    return rows[0].applicant_key


def result_preferences(result: IntraUniversityResult, code: str) -> list[str]:
    best: dict[str, int] = {}
    for (program_id, _), apps in result.rankings.items():
        for app in apps:
            if app.code != code or app.priority is None:
                continue
            current = best.get(program_id)
            if current is None or app.priority < current:
                best[program_id] = app.priority
    return [p for p, _ in sorted(best.items(), key=lambda kv: (kv[1], kv[0]))]
