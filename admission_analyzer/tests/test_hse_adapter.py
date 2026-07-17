"""Тесты адаптера ВШЭ: detect / parse_program_metadata / normalize / validate."""

from __future__ import annotations

from analyzer.readers import read_table
from analyzer.universities.hse import HSEAdapter


def test_detect_matches_hse_format(hse_fixture_path):
    adapter = HSEAdapter()
    raw = read_table(hse_fixture_path)
    detection = adapter.detect(raw, hse_fixture_path)

    assert detection.matched
    assert detection.confidence >= 0.5
    assert detection.adapter_name == "hse"


def test_parse_program_metadata_extracts_places_and_name(hse_fixture_path):
    adapter = HSEAdapter()
    raw = read_table(hse_fixture_path)
    metadata = adapter.parse_program_metadata(raw, hse_fixture_path)

    assert metadata.program_name == "Компьютерные технологии, системы и сети"
    assert metadata.total_places == 50
    assert metadata.updated_at is not None
    assert metadata.updated_at.year == 2026
    assert metadata.program_id.startswith("hse:")


def test_normalize_applications_maps_fields(hse_fixture_path):
    adapter = HSEAdapter()
    raw = read_table(hse_fixture_path)
    metadata = adapter.parse_program_metadata(raw, hse_fixture_path)
    applications = adapter.normalize_applications(raw, metadata)

    assert len(applications) == 7
    row2 = next(a for a in applications if a.code == "2000002")
    assert row2.total_score == 271
    assert row2.exam_sum == 263
    assert row2.exam_1 == 90 and row2.exam_2 == 88 and row2.exam_3 == 85
    assert row2.individual_score == 8
    assert row2.consent is True
    assert row2.priority == 1


def test_dash_consent_is_none_not_false():
    """«-» в согласии -> None, а не отказ."""
    adapter = HSEAdapter()
    header = (
        "№ п/п\tУникальный идентификатор абитуриента\t"
        "Право поступления без вступительных испытаний\tПриоритет зачисления\t"
        "Наличие согласия на зачисление\tСумма конкурсных баллов\tВсе оценки положительные\tСтатус заявления\n"
    )
    rows = "1\t3000001\tНет\t1\t-\t250\tда\tУчаствует в конкурсе\n"
    from analyzer.readers import read_table_from_bytes
    from pathlib import Path

    raw = read_table_from_bytes((header + rows).encode("utf-8"), "hse_dash.tsv")
    metadata = adapter.parse_program_metadata(raw, Path("hse_dash.tsv"))
    applications = adapter.normalize_applications(raw, metadata)

    assert applications[0].consent is None


def test_withdrawn_application_excluded():
    """9. Отозванное заявление ВШЭ исключается."""
    adapter = HSEAdapter()
    raw = read_table(_hse_fixture_path())
    metadata = adapter.parse_program_metadata(raw, _hse_fixture_path())
    applications = adapter.normalize_applications(raw, metadata)

    withdrawn = next(a for a in applications if a.code == "2000004")
    assert withdrawn.active is False
    assert withdrawn.application_status == "Отозвано поступающим"


def test_documents_withdrawn_marks_inactive():
    adapter = HSEAdapter()
    raw = read_table(_hse_fixture_path())
    metadata = adapter.parse_program_metadata(raw, _hse_fixture_path())
    applications = adapter.normalize_applications(raw, metadata)

    row = next(a for a in applications if a.code == "2000007")
    assert row.documents_withdrawn is True
    assert row.active is False


def test_non_positive_scores_excluded():
    adapter = HSEAdapter()
    raw = read_table(_hse_fixture_path())
    metadata = adapter.parse_program_metadata(raw, _hse_fixture_path())
    applications = adapter.normalize_applications(raw, metadata)

    row = next(a for a in applications if a.code == "2000005")
    assert row.all_exams_passed is False
    assert row.active is False


def test_under_review_status_kept_active_with_note():
    """«На рассмотрении» сохраняется активным, но помечено для настройки."""
    adapter = HSEAdapter()
    raw = read_table(_hse_fixture_path())
    metadata = adapter.parse_program_metadata(raw, _hse_fixture_path())
    applications = adapter.normalize_applications(raw, metadata)

    row = next(a for a in applications if a.code == "2000006")
    assert row.active is True
    assert row.application_status == "На рассмотрении"
    assert row.normalization_notes


def test_bvi_recognized_in_hse():
    """10. БВИ ВШЭ распознаётся."""
    adapter = HSEAdapter()
    raw = read_table(_hse_fixture_path())
    metadata = adapter.parse_program_metadata(raw, _hse_fixture_path())
    applications = adapter.normalize_applications(raw, metadata)

    bvi_row = next(a for a in applications if a.code == "2000001")
    assert bvi_row.bvi is True
    assert bvi_row.bvi_reason == "Победитель олимпиады"
    others = [a for a in applications if a.code != "2000001"]
    assert all(not a.bvi for a in others)


def test_preferential_right_combines_9_and_10():
    adapter = HSEAdapter()
    raw = read_table(_hse_fixture_path())
    metadata = adapter.parse_program_metadata(raw, _hse_fixture_path())
    applications = adapter.normalize_applications(raw, metadata)

    row = next(a for a in applications if a.code == "2000006")
    assert row.preferential_right_9 is True
    assert row.preferential_right_10 is False
    assert row.preferential_right is True


def test_priority_paid_fallback_when_priority_missing():
    adapter = HSEAdapter()
    header = (
        "№ п/п\tУникальный идентификатор абитуриента\t"
        "Право поступления без вступительных испытаний\tПриоритет платных мест\t"
        "Наличие согласия на зачисление\tСумма конкурсных баллов\n"
    )
    rows = "1\t3000002\tНет\t3\tда\t250\n"
    from analyzer.readers import read_table_from_bytes
    from pathlib import Path

    raw = read_table_from_bytes((header + rows).encode("utf-8"), "hse_paid.tsv")
    metadata = adapter.parse_program_metadata(raw, Path("hse_paid.tsv"))
    applications = adapter.normalize_applications(raw, metadata)

    assert applications[0].priority == 3


def _hse_fixture_path():
    from pathlib import Path

    return Path(__file__).resolve().parent / "fixtures" / "hse" / "hse_fixture.tsv"
