"""Межвузовский анализ: объединение по глобальному коду + сценарии выбора вуза.

Технически поступление можно узнать по каждому вузу отдельно, но какой из них
человек выберет — по открытым спискам это НЕ определяется однозначно без
согласия или явно заданных пользователем предпочтений. Отсюда два уровня:

* внутривузовское распределение (`allocation.py`) — считается надёжно;
* межвузовский выбор (этот модуль) — вычисляется только там, где сигнал
  однозначен (согласие, предпочтения), иначе статус остаётся
  «неопределённый межвузовский выбор». Ничего не придумывается.

Расхождение в спецификации: в разделе «Межвузовские сценарии» перечислены
`independent / consent_based / user_preferences / optimistic_release /
conservative`, а в разделе «Межвузовский анализ» — `independent /
consent_based / preference_based / probabilistic`. Здесь `user_preferences`
реализован как `preference_based` (то же самое), а `optimistic_release` и
`conservative` — это не отдельные сценарии выбора вуза, а два способа оценить
проходной балл программы (см. `estimate_release_scores`), как и просит раздел
«Сводка по программам».
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .allocation import allocate_university
from .models import (
    ApplicantKey,
    ApplicantProfile,
    Application,
    CompetitionCategory,
    CrossUniversityResult,
    IntraUniversityResult,
    ProgramConfig,
    ProgramMetadata,
    ScenarioConfig,
    UniversityOffer,
)

DEFAULT_PREFERENCES_PATH = Path("config/university_preferences.json")
MIN_PROBABILISTIC_ITERATIONS = 1000

PASSING_STATUS = "проходит"
NOT_PASSING_STATUS = "не проходит"


# --------------------------------------------------------------------------
# Профили абитуриентов
# --------------------------------------------------------------------------


def build_applicant_profiles(
    applications: dict[str, list[Application]]
) -> dict[ApplicantKey, ApplicantProfile]:
    """Объединить заявления одного человека по глобальному коду (все вузы)."""
    profiles: dict[ApplicantKey, ApplicantProfile] = {}
    for apps in applications.values():
        for app in apps:
            profile = profiles.get(app.applicant_key)
            if profile is None:
                profile = ApplicantProfile(applicant_key=app.applicant_key)
                profiles[app.applicant_key] = profile
            profile.applications.append(app)
            profile.universities.add(app.university_code)
    return profiles


# --------------------------------------------------------------------------
# Предложения по вузам
# --------------------------------------------------------------------------


def build_offers(
    profile: ApplicantProfile, results: dict[str, IntraUniversityResult]
) -> list[UniversityOffer]:
    """Одно предложение на вуз: программа, если распределён, иначе «не проходит»."""
    offers: list[UniversityOffer] = []
    for university_code in sorted(profile.universities):
        result = results.get(university_code)
        assigned_program = result.assignment.get(profile.applicant_key) if result else None
        if assigned_program:
            app = next(
                a
                for a in profile.applications
                if a.university_code == university_code and a.program_id == assigned_program
            )
            offers.append(
                UniversityOffer(
                    university_code=university_code,
                    program_id=assigned_program,
                    priority=app.priority,
                    status=PASSING_STATUS,
                    consent=app.consent,
                    total_score=app.total_score,
                )
            )
        else:
            uni_apps = [a for a in profile.applications if a.university_code == university_code]
            consent = next((a.consent for a in uni_apps if a.consent is True), None)
            if consent is None:
                consent = next((a.consent for a in uni_apps if a.consent is False), None)
            offers.append(
                UniversityOffer(
                    university_code=university_code,
                    program_id=None,
                    priority=None,
                    status=NOT_PASSING_STATUS,
                    consent=consent,
                    total_score=None,
                )
            )
    return offers


def _passing(offers: list[UniversityOffer]) -> list[UniversityOffer]:
    return [o for o in offers if o.status == PASSING_STATUS]


# --------------------------------------------------------------------------
# Сценарии выбора вуза
# --------------------------------------------------------------------------


def resolve_independent(applicant_key: ApplicantKey, offers: list[UniversityOffer]) -> CrossUniversityResult:
    passing = _passing(offers)
    ambiguous = len(passing) > 1
    if not passing:
        reason = "не проходит ни в одном из загруженных вузов"
    elif ambiguous:
        reason = "независимый режим: выбор между вузами не разрешается расчётом"
    else:
        reason = f"единственный вуз, где проходит: {passing[0].university_code}"
    return CrossUniversityResult(
        applicant_key=applicant_key,
        offers=offers,
        selected_offer=None,
        selection_reason=reason,
        ambiguous=ambiguous,
    )


def resolve_consent_based(
    applicant_key: ApplicantKey, offers: list[UniversityOffer]
) -> CrossUniversityResult:
    passing = _passing(offers)
    if len(passing) <= 1:
        selected = passing[0] if passing else None
        reason = (
            f"единственный вуз, где проходит: {selected.university_code}"
            if selected
            else "не проходит ни в одном из загруженных вузов"
        )
        return CrossUniversityResult(
            applicant_key=applicant_key, offers=offers, selected_offer=selected,
            selection_reason=reason, ambiguous=False,
        )

    with_consent = [o for o in passing if o.consent is True]
    if len(with_consent) == 1:
        return CrossUniversityResult(
            applicant_key=applicant_key, offers=offers, selected_offer=with_consent[0],
            selection_reason=f"выбран по согласию, поданному только в {with_consent[0].university_code}",
            ambiguous=False,
        )
    if len(with_consent) > 1:
        names = ", ".join(o.university_code for o in with_consent)
        reason = f"неопределённый межвузовский выбор: согласие подано сразу в нескольких вузах ({names})"
    else:
        reason = "неопределённый межвузовский выбор: согласие не подано ни в одном из проходных вузов"
    return CrossUniversityResult(
        applicant_key=applicant_key, offers=offers, selected_offer=None,
        selection_reason=reason, ambiguous=True,
    )


def resolve_preference_based(
    applicant_key: ApplicantKey, offers: list[UniversityOffer], preference_order: list[str]
) -> CrossUniversityResult:
    passing = _passing(offers)
    if not passing:
        return CrossUniversityResult(
            applicant_key=applicant_key, offers=offers, selected_offer=None,
            selection_reason="не проходит ни в одном из загруженных вузов", ambiguous=False,
        )
    by_code = {o.university_code: o for o in passing}
    for university_code in preference_order:
        if university_code in by_code:
            return CrossUniversityResult(
                applicant_key=applicant_key, offers=offers, selected_offer=by_code[university_code],
                selection_reason=f"выбран по заданному порядку предпочтений вузов: {university_code}",
                ambiguous=False,
            )
    # Проходит только там, где предпочтение не задано — порядок неполный.
    names = ", ".join(sorted(by_code))
    return CrossUniversityResult(
        applicant_key=applicant_key, offers=offers, selected_offer=None,
        selection_reason=(
            f"неопределённый межвузовский выбор: проходит в {names}, но порядок "
            "предпочтений для этих вузов не задан"
        ),
        ambiguous=len(passing) > 1,
    )


def resolve_cross_university(
    profile: ApplicantProfile,
    results: dict[str, IntraUniversityResult],
    scenario: str,
    preference_order: Optional[list[str]] = None,
) -> CrossUniversityResult:
    offers = build_offers(profile, results)
    key = profile.applicant_key
    if scenario == "independent":
        return resolve_independent(key, offers)
    if scenario == "consent_based":
        return resolve_consent_based(key, offers)
    if scenario == "preference_based":
        return resolve_preference_based(key, offers, preference_order or [])
    raise ValueError(f"Неизвестный межвузовский сценарий: {scenario}")


def resolve_all(
    profiles: dict[ApplicantKey, ApplicantProfile],
    results: dict[str, IntraUniversityResult],
    scenario: str,
    preferences: Optional[dict] = None,
) -> dict[ApplicantKey, CrossUniversityResult]:
    preferences = preferences or {}
    default_order = preferences.get("default", [])
    per_applicant = preferences.get("applicants", {})
    output: dict[ApplicantKey, CrossUniversityResult] = {}
    for key, profile in profiles.items():
        order = per_applicant.get(str(key), default_order)
        output[key] = resolve_cross_university(profile, results, scenario, order)
    return output


# --------------------------------------------------------------------------
# Предпочтения вузов (config/university_preferences.json)
# --------------------------------------------------------------------------


def load_university_preferences(path: str | Path = DEFAULT_PREFERENCES_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return {"default": [], "applicants": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"default": [], "applicants": {}}
    if not isinstance(raw, dict):
        return {"default": [], "applicants": {}}
    raw.setdefault("default", [])
    raw.setdefault("applicants", {})
    return raw


def save_university_preferences(
    preferences: dict, path: str | Path = DEFAULT_PREFERENCES_PATH
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Оптимистический / консервативный проходной балл (метрики программы)
# --------------------------------------------------------------------------


def estimate_release_scores(
    program_id: str,
    result: IntraUniversityResult,
    ambiguous_codes: set[str],
) -> tuple[Optional[int], Optional[int], int]:
    """(оптимистический проходной, консервативный проходной, освободится мест).

    Консервативный — как есть в базовом внутривузовском расчёте (человек с
    неопределённым межвузовским статусом считается конкурентом). Оптимистический
    — как если бы все такие люди среди уже зачисленных на общий конкурс этой
    программы ушли в другой вуз: их убираем из ранжированного списка целиком,
    остальные сдвигаются вверх на освободившиеся места.
    """
    pr = result.programs.get(program_id)
    if pr is None:
        return None, None, 0
    conservative = pr.passing_score

    ranked = [
        s
        for s in result.rankings.get((program_id, CompetitionCategory.GENERAL.value), [])
    ]
    capacity = pr.general_seats
    filtered = [a for a in ranked if a.code not in ambiguous_codes]
    admitted_optimistic = filtered[:capacity]
    optimistic = admitted_optimistic[-1].total_score if admitted_optimistic else None

    freed = sum(
        1
        for s in pr.allocated
        if not s.bvi and s.category is CompetitionCategory.GENERAL and s.application.code in ambiguous_codes
    )
    return optimistic, conservative, freed


def ambiguous_codes_for_university(
    cross_results: dict[ApplicantKey, CrossUniversityResult], university_code: str
) -> set[str]:
    """Коды, которые проходят в этом вузе, но межвузовский выбор для них не определён."""
    codes: set[str] = set()
    for key, cross in cross_results.items():
        if not cross.ambiguous:
            continue
        if any(o.university_code == university_code and o.status == PASSING_STATUS for o in cross.offers):
            codes.add(key.applicant_id)
    return codes


# --------------------------------------------------------------------------
# Вероятностный (Monte Carlo) сценарий — экспериментальный
# --------------------------------------------------------------------------


@dataclass
class ProbabilisticResult:
    """Результат Monte Carlo-симуляции межвузовского выбора.

    ЭКСПЕРИМЕНТАЛЬНЫЙ режим: не официальный прогноз, а иллюстрация того, как
    менялась бы картина при заданных пользователем вероятностях выбора вуза.
    """

    iterations: int
    applicant_pass_probability: dict[str, float] = field(default_factory=dict)
    program_score_samples: dict[str, list[int]] = field(default_factory=dict)
    program_average_freed_seats: dict[str, float] = field(default_factory=dict)


def simulate_probabilistic(
    programs: dict[str, ProgramMetadata],
    configs: dict[str, ProgramConfig],
    applications: dict[str, list[Application]],
    scenario: ScenarioConfig,
    university_probabilities: dict[str, float],
    profiles: dict[ApplicantKey, ApplicantProfile],
    base_results: dict[str, IntraUniversityResult],
    cross_results: dict[ApplicantKey, CrossUniversityResult],
    n_iterations: int = MIN_PROBABILISTIC_ITERATIONS,
    seed: Optional[int] = None,
) -> ProbabilisticResult:
    """Monte Carlo не менее 1000 прогонов (ограничение снизу, см. ТЗ).

    Для каждого неопределённого (ambiguous) абитуриента на каждой итерации
    случайно выбирается «сохраняемый» вуз (по нормализованным на его проходные
    вузы вероятностям), его заявления в остальных вузах на этой итерации не
    учитываются, после чего перераспределение пересчитывается заново для
    затронутых вузов.
    """
    n_iterations = max(n_iterations, MIN_PROBABILISTIC_ITERATIONS)
    rng = random.Random(seed)

    ambiguous_keys = [key for key, cross in cross_results.items() if cross.ambiguous]
    pass_counts: dict[str, int] = defaultdict(int)
    score_samples: dict[str, list[int]] = defaultdict(list)
    freed_totals: dict[str, float] = defaultdict(float)

    universities = sorted({p.university_code for p in programs.values()})

    for _ in range(n_iterations):
        excluded_codes: dict[str, set[str]] = defaultdict(set)
        kept_university: dict[ApplicantKey, str] = {}

        for key in ambiguous_keys:
            passing_unis = [
                o.university_code
                for o in cross_results[key].offers
                if o.status == PASSING_STATUS
            ]
            if not passing_unis:
                continue
            weights = [max(university_probabilities.get(u, 0.0), 0.0) for u in passing_unis]
            if sum(weights) <= 0:
                weights = [1.0] * len(passing_unis)
            kept = rng.choices(passing_unis, weights=weights, k=1)[0]
            kept_university[key] = kept
            for university_code in passing_unis:
                if university_code != kept:
                    excluded_codes[university_code].add(key.applicant_id)

        affected_universities = {u for u in excluded_codes if excluded_codes[u]}
        iteration_results: dict[str, IntraUniversityResult] = dict(base_results)
        for university_code in affected_universities:
            filtered_applications = {
                pid: [a for a in apps if a.code not in excluded_codes[university_code]]
                for pid, apps in applications.items()
                if programs[pid].university_code == university_code
            }
            iteration_results[university_code] = allocate_university(
                university_code, programs, configs, filtered_applications, scenario
            )

        for key in ambiguous_keys:
            kept = kept_university.get(key)
            if kept is None:
                continue
            passed = iteration_results[kept].assignment.get(key) is not None
            if passed:
                pass_counts[str(key)] += 1

        for university_code in affected_universities:
            for program_id, pr in iteration_results[university_code].programs.items():
                if pr.passing_score is not None:
                    score_samples[program_id].append(pr.passing_score)
                freed_totals[program_id] += pr.free_seats

    result = ProbabilisticResult(iterations=n_iterations)
    for key in ambiguous_keys:
        result.applicant_pass_probability[str(key)] = pass_counts[str(key)] / n_iterations
    for program_id, samples in score_samples.items():
        result.program_score_samples[program_id] = samples
    for program_id, total in freed_totals.items():
        result.program_average_freed_seats[program_id] = total / n_iterations
    return result
