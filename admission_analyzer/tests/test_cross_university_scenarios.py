"""Тесты межвузовских сценариев выбора и оценок проходного балла."""

from __future__ import annotations

from analyzer.allocation import allocate_all
from analyzer.cross_university import (
    ambiguous_codes_for_university,
    build_applicant_profiles,
    estimate_release_scores,
    resolve_all,
)
from analyzer.models import ApplicantKey, INTRA_SCENARIOS
from conftest import CAMPAIGN_YEAR, make_app, make_config, make_metadata

ANY = INTRA_SCENARIOS["highest_priority"]


def _dual_passing_setup(itmo_consent, hse_consent):
    """Абитуриент 100 проходит и в ИТМО, и в ВШЭ, с заданными согласиями."""
    apps_by_program = {
        "itmo:A": [make_app("100", "itmo:A", university_code="itmo", priority=1, total_score=300, consent=itmo_consent)],
        "hse:B": [make_app("100", "hse:B", university_code="hse", priority=1, total_score=270, consent=hse_consent)],
    }
    programs = {pid: make_metadata(pid, apps[0].university_code) for pid, apps in apps_by_program.items()}
    configs = {pid: make_config(1) for pid in apps_by_program}
    results = allocate_all(programs, configs, apps_by_program, ANY)
    profiles = build_applicant_profiles(apps_by_program)
    return profiles, results


def test_consent_in_one_university_determines_choice():
    """6. Согласие в одном вузе определяет межвузовский выбор в consent_based."""
    profiles, results = _dual_passing_setup(itmo_consent=True, hse_consent=False)
    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")

    cross = resolve_all(profiles, results, "consent_based")[key]

    assert cross.ambiguous is False
    assert cross.selected_offer is not None
    assert cross.selected_offer.university_code == "itmo"


def test_multiple_consents_create_conflict():
    """7. Несколько согласий создают конфликт."""
    profiles, results = _dual_passing_setup(itmo_consent=True, hse_consent=True)
    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")

    cross = resolve_all(profiles, results, "consent_based")[key]

    assert cross.ambiguous is True
    assert cross.selected_offer is None
    assert "нескольких вузах" in cross.selection_reason.lower()


def test_no_consent_gives_undetermined_choice():
    """8. Отсутствие согласий даёt неопределённый выбор."""
    profiles, results = _dual_passing_setup(itmo_consent=False, hse_consent=None)
    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")

    cross = resolve_all(profiles, results, "consent_based")[key]

    assert cross.ambiguous is True
    assert cross.selected_offer is None


def test_independent_scenario_lists_all_passing_without_choosing():
    profiles, results = _dual_passing_setup(itmo_consent=True, hse_consent=True)
    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")

    cross = resolve_all(profiles, results, "independent")[key]

    assert cross.selected_offer is None
    assert cross.ambiguous is True
    assert len(cross.offers) == 2
    assert {o.university_code for o in cross.offers} == {"itmo", "hse"}


def test_preference_based_picks_configured_university():
    profiles, results = _dual_passing_setup(itmo_consent=None, hse_consent=None)
    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")

    cross = resolve_all(profiles, results, "preference_based", {"default": ["hse", "itmo"]})[key]

    assert cross.ambiguous is False
    assert cross.selected_offer.university_code == "hse"


def test_per_applicant_preference_overrides_default():
    profiles, results = _dual_passing_setup(itmo_consent=None, hse_consent=None)
    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")

    preferences = {"default": ["itmo", "hse"], "applicants": {str(key): ["hse", "itmo"]}}
    cross = resolve_all(profiles, results, "preference_based", preferences)[key]

    assert cross.selected_offer.university_code == "hse"


def test_optimistic_release_frees_seat_for_next_candidate():
    """17. Оптимистический сценарий корректно освобождает места."""
    apps_by_program = {
        "itmo:A": [
            make_app("100", "itmo:A", university_code="itmo", priority=1, total_score=300, consent=None),
            make_app("200", "itmo:A", university_code="itmo", priority=1, total_score=290, consent=True),
        ],
        "hse:B": [make_app("100", "hse:B", university_code="hse", priority=1, total_score=270, consent=None)],
    }
    programs = {pid: make_metadata(pid, apps[0].university_code) for pid, apps in apps_by_program.items()}
    configs = {"itmo:A": make_config(1), "hse:B": make_config(1)}
    results = allocate_all(programs, configs, apps_by_program, ANY)
    profiles = build_applicant_profiles(apps_by_program)
    cross = resolve_all(profiles, results, "consent_based")

    key100 = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")
    assert cross[key100].ambiguous is True  # нет согласия ни там, ни там

    ambiguous = ambiguous_codes_for_university(cross, "itmo")
    assert ambiguous == {"100"}

    optimistic, conservative, freed = estimate_release_scores("itmo:A", results["itmo"], ambiguous)

    assert conservative == 300  # консервативно — 100 остаётся, проходной как есть
    assert optimistic == 290  # оптимистично — 100 уходит, 200 становится проходным
    assert freed == 1


def test_conservative_scenario_removes_nobody_without_basis():
    """18. Консервативный сценарий никого не удаляет без основания."""
    apps_by_program = {
        "itmo:A": [
            make_app("100", "itmo:A", university_code="itmo", priority=1, total_score=300, consent=None),
        ],
        "hse:B": [make_app("100", "hse:B", university_code="hse", priority=1, total_score=270, consent=None)],
    }
    programs = {pid: make_metadata(pid, apps[0].university_code) for pid, apps in apps_by_program.items()}
    configs = {"itmo:A": make_config(1), "hse:B": make_config(1)}
    results = allocate_all(programs, configs, apps_by_program, ANY)
    profiles = build_applicant_profiles(apps_by_program)
    cross = resolve_all(profiles, results, "consent_based")

    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")
    ambiguous = ambiguous_codes_for_university(cross, "itmo")
    _, conservative, _ = estimate_release_scores("itmo:A", results["itmo"], ambiguous)

    # Консервативная оценка не меняет фактическое распределение вуза.
    assert conservative == results["itmo"].programs["itmo:A"].passing_score
    assert results["itmo"].assignment[key] == "itmo:A"
    assert results["itmo"].programs["itmo:A"].allocated_count == 1
