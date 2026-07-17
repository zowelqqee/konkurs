"""Реестр адаптеров вузов.

Добавление нового вуза = новый файл `universities/<код>.py` с классом-адаптером
+ одна строка в `ADAPTERS` ниже. См. README, раздел «Добавление нового
адаптера».
"""

from __future__ import annotations

from pathlib import Path

from ..models import DetectionResult
from ..readers import RawTable
from .base import UniversityAdapter
from .hse import HSEAdapter
from .itmo import ITMOAdapter

ADAPTERS: list[UniversityAdapter] = [ITMOAdapter(), HSEAdapter()]

# Файл считается однозначно распознанным, только если лучший результат
# уверенно (с запасом) обходит второго кандидата — иначе это конфликт форматов.
CONFLICT_MARGIN = 0.15


def adapters_by_code() -> dict[str, UniversityAdapter]:
    return {a.university_code: a for a in ADAPTERS}


def detect_all(raw_table: RawTable, file_path: Path) -> list[DetectionResult]:
    """Прогнать файл через все адаптеры, отсортировать по уверенности."""
    results = [adapter.detect(raw_table, file_path) for adapter in ADAPTERS]
    return sorted(results, key=lambda r: r.confidence, reverse=True)


def resolve_adapter(
    raw_table: RawTable, file_path: Path
) -> tuple[UniversityAdapter | None, list[DetectionResult], bool]:
    """Выбрать лучший адаптер.

    Возвращает (адаптер или None, все результаты detect, признак конфликта).
    Конфликт — когда два и более адаптера совпали (matched=True) с близкой
    уверенностью: в этом случае адаптер не выбирается автоматически.
    """
    results = detect_all(raw_table, file_path)
    matched = [r for r in results if r.matched]
    if not matched:
        return None, results, False
    if len(matched) > 1 and (matched[0].confidence - matched[1].confidence) < CONFLICT_MARGIN:
        return None, results, True
    by_code = adapters_by_code()
    return by_code[matched[0].adapter_name], results, False
