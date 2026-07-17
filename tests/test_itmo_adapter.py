"""Тесты адаптера ИТМО: detect / parse_program_metadata / normalize / validate."""

from __future__ import annotations

from analyzer.models import CompetitionCategory
from analyzer.readers import read_table
from analyzer.universities.itmo import ITMOAdapter


def test_detect_matches_itmo_format(itmo_fixture_path):
    adapter = ITMOAdapter()
    raw = read_table(itmo_fixture_path)
    detection = adapter.detect(raw, itmo_fixture_path)

    assert detection.matched
    assert detection.confidence >= 0.5
    assert detection.adapter_name == "itmo"
    assert detection.reasons


def test_parse_program_metadata_extracts_code_and_name(itmo_fixture_path):
    adapter = ITMOAdapter()
    raw = read_table(itmo_fixture_path)
    metadata = adapter.parse_program_metadata(raw, itmo_fixture_path)

    assert metadata.field_code == "09.03.03"
    assert "тестовая программа" in metadata.program_name
    assert metadata.university_code == "itmo"
    assert metadata.program_id.startswith("itmo:09.03.03:")
    assert metadata.program_id.endswith(":budget")


def test_normalize_applications_maps_fields(itmo_fixture_path):
    adapter = ITMOAdapter()
    raw = read_table(itmo_fixture_path)
    metadata = adapter.parse_program_metadata(raw, itmo_fixture_path)
    applications = adapter.normalize_applications(raw, metadata)

    assert len(applications) == 6
    by_code = {a.code for a in applications}
    assert by_code == {"1000001", "1000002", "1000003", "1000004", "1000005", "1000006"}

    row3 = next(a for a in applications if a.code == "1000003")
    assert row3.total_score == 310
    assert row3.exam_sum == 300
    assert row3.exam_1 == 100 and row3.exam_2 == 100 and row3.exam_3 == 100
    assert row3.individual_score == 10
    assert row3.consent is True
    assert row3.competition_category is CompetitionCategory.GENERAL
    assert row3.bvi is False

    quota_row = next(a for a in applications if a.code == "1000006")
    assert quota_row.competition_category is CompetitionCategory.SPECIAL_QUOTA
    assert quota_row.preferential_right is True


def test_bvi_recognized_in_itmo(itmo_fixture_path):
    """11. БВИ ИТМО распознаётся."""
    adapter = ITMOAdapter()
    raw = read_table(itmo_fixture_path)
    metadata = adapter.parse_program_metadata(raw, itmo_fixture_path)
    applications = adapter.normalize_applications(raw, metadata)

    bvi_rows = [a for a in applications if a.bvi]
    assert {a.code for a in bvi_rows} == {"1000001", "1000002"}
    for app in bvi_rows:
        assert app.bvi_reason and "олимпиада" in app.bvi_reason.lower()
    # БВИ ранжируются по месту, поэтому исходное место должно сохраниться.
    assert {a.source_rank for a in bvi_rows} == {1, 2}


def test_codes_are_strings_with_no_float_artifacts(itmo_fixture_path):
    adapter = ITMOAdapter()
    raw = read_table(itmo_fixture_path)
    metadata = adapter.parse_program_metadata(raw, itmo_fixture_path)
    applications = adapter.normalize_applications(raw, metadata)

    for app in applications:
        assert isinstance(app.code, str)
        assert "." not in app.code


def test_validate_flags_unknown_category_and_missing_priority():
    adapter = ITMOAdapter()
    header = "Категория\tМесто\tКод\tПриоритет\tБалл ВИ+ИД\n"
    rows = "Ерунда\t1\t999\t\t300\n"
    from analyzer.readers import read_table_from_bytes

    raw = read_table_from_bytes((header + rows).encode("utf-8"), "x.tsv")
    from pathlib import Path

    metadata = adapter.parse_program_metadata(raw, Path("x.tsv"))
    applications = adapter.normalize_applications(raw, metadata)
    warnings = adapter.validate(applications, metadata)

    kinds = {w.kind for w in warnings}
    assert {"missing_priority", "unknown_category"} <= kinds


def test_empty_file_produces_no_applications(tmp_path):
    adapter = ITMOAdapter()
    path = tmp_path / "090303 empty.tsv"
    path.write_text("", encoding="utf-8")
    raw = read_table(path)
    metadata = adapter.parse_program_metadata(raw, path)
    applications = adapter.normalize_applications(raw, metadata)

    assert applications == []
    assert metadata.field_code == "09.03.03"
