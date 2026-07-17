"""Тесты объединения абитуриента по глобальному коду между вузами."""

from __future__ import annotations

from analyzer.allocation import allocate_all
from analyzer.cross_university import build_applicant_profiles
from analyzer.models import ApplicantKey, INTRA_SCENARIOS
from conftest import CAMPAIGN_YEAR, make_app, make_config, make_metadata

ANY = INTRA_SCENARIOS["highest_priority"]


def _setup(apps_by_program: dict[str, list]):
    programs = {}
    for pid, apps in apps_by_program.items():
        uni = apps[0].university_code
        programs[pid] = make_metadata(pid, uni)
    configs = {pid: make_config(1) for pid in apps_by_program}
    return programs, configs


def test_same_code_merges_into_one_profile():
    """1. Один и тот же код в ИТМО и ВШЭ объединяется в один ApplicantProfile."""
    apps_by_program = {
        "itmo:A": [make_app("1401780", "itmo:A", university_code="itmo", priority=1, total_score=300)],
        "hse:B": [make_app("1401780", "hse:B", university_code="hse", priority=1, total_score=270)],
    }
    profiles = build_applicant_profiles(apps_by_program)
    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="1401780")

    assert key in profiles
    profile = profiles[key]
    assert profile.universities == {"itmo", "hse"}
    assert len(profile.applications) == 2


def test_priorities_across_universities_are_not_compared():
    """2. Приоритет 1 в одном вузе не «выше» приоритета 4 в другом — независимые оси."""
    apps_by_program = {
        "itmo:A": [make_app("100", "itmo:A", university_code="itmo", priority=4, total_score=300)],
        "hse:B": [make_app("100", "hse:B", university_code="hse", priority=1, total_score=270)],
    }
    programs, configs = _setup(apps_by_program)
    results = allocate_all(programs, configs, apps_by_program, ANY)

    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")
    # Приоритет 4 в ИТМО не мешает пройти — внутри ИТМО это единственная заявка.
    assert results["itmo"].assignment[key] == "itmo:A"
    assert results["hse"].assignment[key] == "hse:B"


def test_one_program_per_university_simultaneously():
    """3. Человек получает одну программу внутри ИТМО и одну внутри ВШЭ одновременно."""
    apps_by_program = {
        "itmo:A": [
            make_app("100", "itmo:A", university_code="itmo", priority=1, total_score=300),
            make_app("100", "itmo:B", university_code="itmo", priority=2, total_score=300),
        ],
        "itmo:B": [make_app("100", "itmo:B", university_code="itmo", priority=2, total_score=300)],
        "hse:C": [make_app("100", "hse:C", university_code="hse", priority=1, total_score=270)],
    }
    programs, configs = _setup(apps_by_program)
    results = allocate_all(programs, configs, apps_by_program, ANY)

    key = ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id="100")
    assert results["itmo"].assignment[key] == "itmo:A"
    assert "itmo:B" not in {results["itmo"].assignment.get(key)}  # только одна программа внутри ИТМО
    assert results["hse"].assignment[key] == "hse:C"
    # Ровно одна программа в каждом вузе — не две сразу в одном.
    itmo_seats = sum(1 for pr in results["itmo"].programs.values() for s in pr.allocated if s.application.code == "100")
    assert itmo_seats == 1


def test_same_code_different_years_not_merged():
    """15. Одинаковый код в разных годах не объединяется."""
    apps_2026 = {"A": [make_app("777", "A", university_code="itmo", priority=1, total_score=300, campaign_year=2026)]}
    apps_2025 = {"B": [make_app("777", "B", university_code="itmo", priority=1, total_score=300, campaign_year=2025)]}
    merged = {**apps_2026, **apps_2025}

    profiles = build_applicant_profiles(merged)

    key_2026 = ApplicantKey(campaign_year=2026, applicant_id="777")
    key_2025 = ApplicantKey(campaign_year=2025, applicant_id="777")
    assert key_2026 in profiles
    assert key_2025 in profiles
    assert key_2026 != key_2025
    assert len(profiles[key_2026].applications) == 1
    assert len(profiles[key_2025].applications) == 1
