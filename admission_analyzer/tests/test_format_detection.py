"""Тесты определения формата и реестра адаптеров."""

from __future__ import annotations

from pathlib import Path

from analyzer.readers import read_table, read_table_from_bytes
from analyzer.universities.registry import detect_all, resolve_adapter


def test_itmo_file_detected_by_itmo_adapter(itmo_fixture_path):
    """12. Файл ИТМО определяется ITMOAdapter."""
    raw = read_table(itmo_fixture_path)
    adapter, detections, conflict = resolve_adapter(raw, itmo_fixture_path)

    assert conflict is False
    assert adapter is not None
    assert adapter.university_code == "itmo"
    hse_result = next(d for d in detections if d.adapter_name == "hse")
    assert hse_result.matched is False


def test_hse_file_detected_by_hse_adapter(hse_fixture_path):
    """13. Файл ВШЭ определяется HSEAdapter."""
    raw = read_table(hse_fixture_path)
    adapter, detections, conflict = resolve_adapter(raw, hse_fixture_path)

    assert conflict is False
    assert adapter is not None
    assert adapter.university_code == "hse"
    itmo_result = next(d for d in detections if d.adapter_name == "itmo")
    assert itmo_result.matched is False


def test_conflicting_file_is_not_silently_loaded():
    """Два адаптера с одинаково высокой уверенностью -> конфликт, не молчаливый выбор."""
    header = (
        "Категория\tМесто\tКод\tПриоритет\tБалл ВИ+ИД\tСогласие\t"
        "Уникальный идентификатор абитуриента\tПриоритет зачисления\t"
        "Наличие согласия на зачисление\tСумма конкурсных баллов\t"
        "Все оценки положительные\tСтатус заявления\t"
        "Право поступления без вступительных испытаний\n"
    )
    rows = "Общий конкурс\t1\t100\t1\t300\tда\t100\t1\tда\t300\tда\tУчаствует в конкурсе\tНет\n"
    raw = read_table_from_bytes((header + rows).encode("utf-8"), "ambiguous.tsv")

    adapter, detections, conflict = resolve_adapter(raw, Path("ambiguous.tsv"))

    assert conflict is True
    assert adapter is None
    matched = [d for d in detections if d.matched]
    assert len(matched) >= 2


def test_unknown_format_matches_nothing():
    header = "Столбец А\tСтолбец Б\n"
    rows = "1\t2\n"
    raw = read_table_from_bytes((header + rows).encode("utf-8"), "unknown.tsv")

    adapter, detections, conflict = resolve_adapter(raw, Path("unknown.tsv"))

    assert adapter is None
    assert conflict is False
    assert all(not d.matched for d in detections)


def test_detect_all_sorted_by_confidence_descending(itmo_fixture_path):
    raw = read_table(itmo_fixture_path)
    detections = detect_all(raw, itmo_fixture_path)

    confidences = [d.confidence for d in detections]
    assert confidences == sorted(confidences, reverse=True)
