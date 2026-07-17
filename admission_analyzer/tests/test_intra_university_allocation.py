"""Тесты внутривузового распределения (отложенное принятие)."""

from __future__ import annotations

import itertools

from analyzer.allocation import allocate_university
from analyzer.models import CompetitionCategory, INTRA_SCENARIOS
from conftest import make_app, make_config, make_metadata

ANY = INTRA_SCENARIOS["highest_priority"]


def _run(university_code, programs_apps, configs=None, scenario=ANY):
    """programs_apps: dict[program_id, list[Application]]."""
    programs = {pid: make_metadata(pid, university_code) for pid in programs_apps}
    configs = configs or {pid: make_config(1) for pid in programs_apps}
    return allocate_university(university_code, programs, configs, programs_apps, scenario)


def test_stays_on_higher_priority_when_passing_both():
    """4/5. Внутри вуза абитуриент остаётся на более высоком доступном приоритете."""
    apps = {
        "A": [make_app("100", "A", priority=1, total_score=300)],
        "B": [make_app("100", "B", priority=2, total_score=300)],
    }
    result = _run("itmo", apps)

    assert result.assignment[_key("100")] == "A"
    assert result.programs["B"].allocated_count == 0
    assert result.programs["B"].left_for_higher == 1


def test_hse_priority_retention_symmetrical():
    """5. То же самое для ВШЭ — движок один и тот же, вуз параметризован."""
    apps = {
        "X": [make_app("200", "X", university_code="hse", priority=1, total_score=290)],
        "Y": [make_app("200", "Y", university_code="hse", priority=3, total_score=290)],
    }
    result = _run("hse", apps)

    assert result.assignment[_key("200")] == "X"
    assert result.programs["Y"].left_for_higher == 1


def test_departure_frees_seat_for_next_in_line():
    """16. Освобождение места внутри вуза передаёт его следующему."""
    apps = {
        "A": [make_app("100", "A", priority=1, total_score=300)],
        "B": [
            make_app("100", "B", priority=2, total_score=300),
            make_app("200", "B", priority=1, total_score=250),
        ],
    }
    result = _run("itmo", apps)

    assert result.assignment[_key("100")] == "A"
    assert result.assignment[_key("200")] == "B"
    assert result.programs["B"].last_admitted_id == "200"


def test_chain_reallocation_across_three_programs():
    apps = {
        "A": [
            make_app("100", "A", priority=1, total_score=300),
            make_app("200", "A", priority=1, total_score=290),
        ],
        "B": [
            make_app("200", "B", priority=2, total_score=290),
            make_app("300", "B", priority=1, total_score=280),
        ],
        "C": [
            make_app("300", "C", priority=2, total_score=280),
            make_app("400", "C", priority=1, total_score=270),
        ],
    }
    result = _run("itmo", apps)

    assert result.assignment[_key("100")] == "A"
    assert result.assignment[_key("200")] == "B"
    assert result.assignment[_key("300")] == "C"
    assert _key("400") not in result.assignment


def test_bvi_consume_budget_seats():
    apps = {
        "A": [
            make_app("900", "A", priority=1, category=CompetitionCategory.GENERAL, bvi=True, source_rank=1),
            make_app("901", "A", priority=1, category=CompetitionCategory.GENERAL, bvi=True, source_rank=2),
            make_app("100", "A", priority=1, total_score=300),
            make_app("200", "A", priority=1, total_score=290),
        ],
    }
    configs = {"A": make_config(3, bvi_within_budget=True)}
    result = _run("itmo", apps, configs)
    pr = result.programs["A"]

    assert pr.bvi_count == 2
    assert pr.general_seats == 1
    assert result.assignment[_key("100")] == "A"
    assert _key("200") not in result.assignment


def test_bvi_ranked_by_source_rank_not_score():
    apps = {
        "A": [
            make_app("low", "A", priority=1, category=CompetitionCategory.GENERAL, bvi=True, source_rank=1, total_score=0),
            make_app("high", "A", priority=1, category=CompetitionCategory.GENERAL, bvi=True, source_rank=2, total_score=300),
        ],
    }
    configs = {"A": make_config(1)}
    result = _run("itmo", apps, configs)

    assert result.assignment == {_key("low"): "A"}


def test_quota_tracked_separately_from_general():
    apps = {
        "A": [
            make_app("q1", "A", priority=1, category=CompetitionCategory.SPECIAL_QUOTA, total_score=100),
            make_app("q2", "A", priority=1, category=CompetitionCategory.SPECIAL_QUOTA, total_score=90),
            make_app("g1", "A", priority=1, total_score=300),
        ],
    }
    configs = {"A": make_config(3, special_quota_places=1, redistribute_unfilled_quota=False)}
    result = _run("itmo", apps, configs)
    pr = result.programs["A"]

    assert pr.quota_filled["особая квота"] == 1
    assert result.assignment[_key("q1")] == "A"
    assert _key("q2") not in result.assignment
    assert result.assignment[_key("g1")] == "A"
    assert pr.general_seats == 2


def test_equal_scores_resolved_deterministically():
    apps = [
        make_app("222", "A", priority=1, total_score=300),
        make_app("111", "A", priority=1, total_score=300),
    ]
    first = _run("itmo", {"A": list(apps)}, {"A": make_config(1)})
    second = _run("itmo", {"A": list(reversed(apps))}, {"A": make_config(1)})

    assert first.assignment == second.assignment == {_key("111"): "A"}


def test_result_independent_of_program_and_row_order():
    """14. Порядок файлов не влияет на результат."""
    apps_a = [
        make_app("100", "A", priority=1, total_score=300),
        make_app("200", "A", priority=1, total_score=290),
        make_app("300", "A", priority=2, total_score=295),
    ]
    apps_b = [
        make_app("200", "B", priority=2, total_score=290),
        make_app("300", "B", priority=1, total_score=295),
        make_app("400", "B", priority=1, total_score=280),
    ]
    configs = {"A": make_config(1), "B": make_config(1)}

    baseline = None
    for order_a, order_b, swap in itertools.product(
        itertools.permutations(apps_a), itertools.permutations(apps_b), [False, True]
    ):
        programs_apps = {"A": list(order_a), "B": list(order_b)}
        if swap:
            programs_apps = {"B": list(order_b), "A": list(order_a)}
        result = _run("itmo", programs_apps, configs)
        if baseline is None:
            baseline = result.assignment
        assert result.assignment == baseline


def test_converges_and_reports_iterations():
    apps = {
        "A": [make_app(str(i), "A", priority=1, total_score=300 - i) for i in range(50)],
        "B": [make_app(str(i), "B", priority=2, total_score=300 - i) for i in range(50)],
    }
    configs = {"A": make_config(10), "B": make_config(10)}
    result = _run("itmo", apps, configs)

    assert result.converged
    assert result.iterations > 0
    assert result.programs["A"].allocated_count == 10
    assert result.programs["B"].allocated_count == 10
    assert len(result.assignment) == 20


def _key(code: str):
    from conftest import CAMPAIGN_YEAR
    from analyzer.models import ApplicantKey

    return ApplicantKey(campaign_year=CAMPAIGN_YEAR, applicant_id=code)
