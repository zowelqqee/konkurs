"""Поиск файлов, определение вуза, разрешение версий программ, настройки мест.

Всё, что происходит до распределения: файл → RawTable → адаптер вуза →
ProgramMetadata + Application[] → группировка по program_id → выбор последней
версии, если одна программа встретилась в нескольких файлах.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import (
    Application,
    ApplicantKey,
    DataWarning,
    FileRecord,
    ProgramConfig,
    ProgramMetadata,
)
from .readers import ReaderError, RawTable, find_data_files, read_table, read_table_from_bytes
from .universities.registry import resolve_adapter

DEFAULT_CAMPAIGN_YEAR = 2026
DEFAULT_CONFIG_PATH = Path("config/programs.json")


@dataclass
class DiscoveryResult:
    """Итог загрузки всей папки с конкурсными списками."""

    programs: dict[str, ProgramMetadata] = field(default_factory=dict)
    applications: dict[str, list[Application]] = field(default_factory=dict)
    files: list[FileRecord] = field(default_factory=list)
    warnings: list[DataWarning] = field(default_factory=list)

    def all_applications(self) -> list[Application]:
        return [a for apps in self.applications.values() for a in apps]

    def universities(self) -> set[str]:
        return {p.university_code for p in self.programs.values()}


def _set_campaign_year(applications: list[Application], campaign_year: int) -> list[Application]:
    """Проставить год кампании — адаптеры его не знают (год не входит в файл)."""
    for app in applications:
        app.applicant_key = ApplicantKey(
            campaign_year=campaign_year, applicant_id=app.applicant_key.applicant_id
        )
    return applications


def _load_one(
    raw_table: RawTable, file_path: Path, campaign_year: int
) -> tuple[Optional[ProgramMetadata], list[Application], list[DataWarning], FileRecord]:
    adapter, detections, conflict = resolve_adapter(raw_table, file_path)
    warnings: list[DataWarning] = []

    if conflict:
        names = ", ".join(f"{d.adapter_name} ({d.confidence:.0%})" for d in detections if d.matched)
        warnings.append(
            DataWarning(
                kind="format_conflict",
                severity="error",
                message=f"«{file_path.name}»: конфликт форматов — несколько адаптеров подходят одинаково хорошо ({names}). Файл не загружен.",
            )
        )
        record = FileRecord(
            path=file_path, university_code=None, university_name=None, format_ok=False,
            confidence=detections[0].confidence if detections else 0.0, program_id=None,
            program_name=None, updated_at=None, row_count=len(raw_table.dataframe),
            dropped=True, drop_reason="конфликт форматов",
        )
        return None, [], warnings, record

    if adapter is None:
        warnings.append(
            DataWarning(
                kind="unknown_format",
                severity="error",
                message=f"«{file_path.name}»: формат не распознан ни одним адаптером.",
            )
        )
        record = FileRecord(
            path=file_path, university_code=None, university_name=None, format_ok=False,
            confidence=detections[0].confidence if detections else 0.0, program_id=None,
            program_name=None, updated_at=None, row_count=len(raw_table.dataframe),
            dropped=True, drop_reason="формат не распознан",
        )
        return None, [], warnings, record

    metadata = adapter.parse_program_metadata(raw_table, file_path)
    applications = adapter.normalize_applications(raw_table, metadata)
    applications = _set_campaign_year(applications, campaign_year)
    warnings.extend(adapter.validate(applications, metadata))

    best = next(d for d in detections if d.adapter_name == adapter.university_code)
    record = FileRecord(
        path=file_path,
        university_code=adapter.university_code,
        university_name=adapter.university_name,
        format_ok=True,
        confidence=best.confidence,
        program_id=metadata.program_id,
        program_name=metadata.program_name,
        updated_at=metadata.updated_at,
        row_count=len(applications),
    )
    return metadata, applications, warnings, record


def _resolve_versions(
    loaded: list[tuple[ProgramMetadata, list[Application], FileRecord]]
) -> tuple[dict[str, tuple[ProgramMetadata, list[Application]]], list[DataWarning]]:
    """Если программа встретилась в нескольких файлах — оставить самую свежую."""
    by_program: dict[str, list[tuple[ProgramMetadata, list[Application], FileRecord]]] = {}
    for metadata, applications, record in loaded:
        by_program.setdefault(metadata.program_id, []).append((metadata, applications, record))

    kept: dict[str, tuple[ProgramMetadata, list[Application]]] = {}
    warnings: list[DataWarning] = []
    for program_id, versions in by_program.items():
        if len(versions) == 1:
            metadata, applications, _ = versions[0]
            kept[program_id] = (metadata, applications)
            continue

        def sort_key(item):
            metadata, _, record = item
            if metadata.updated_at is not None:
                return metadata.updated_at
            return datetime.fromtimestamp(record.path.stat().st_mtime)

        versions.sort(key=sort_key, reverse=True)
        best_metadata, best_applications, best_record = versions[0]
        kept[program_id] = (best_metadata, best_applications)
        dropped_names = [r.path.name for _, _, r in versions[1:]]
        for _, _, record in versions[1:]:
            record.dropped = True
            record.drop_reason = f"старее файла {best_record.path.name}"
        warnings.append(
            DataWarning(
                kind="duplicate_program_version",
                severity="info",
                program_id=program_id,
                message=(
                    f"Программа «{best_metadata.title}» встретилась в {len(versions)} файлах. "
                    f"Использован самый свежий: «{best_record.path.name}». "
                    f"Отброшены: {', '.join(dropped_names)}."
                ),
            )
        )
    return kept, warnings


def _dedupe_warnings(warnings: list[DataWarning]) -> list[DataWarning]:
    """Убрать буквально повторяющиеся предупреждения.

    Реальные выгрузки списков иногда содержат дублирующиеся блоки строк
    (одна и та же строка абитуриента повторена в файле несколько раз) — тогда
    построчная проверка адаптера выдаёт одно и то же предупреждение много раз.
    Дублирующиеся строки сами по себе — повод для отдельного предупреждения
    (`duplicate_code_in_program`), а не для многократного повтора остальных.
    """
    seen: set[tuple] = set()
    result: list[DataWarning] = []
    for warning in warnings:
        key = (warning.kind, warning.university_code, warning.program_id, warning.applicant_id, warning.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result


def load_directory(
    directory: str | Path, campaign_year: int = DEFAULT_CAMPAIGN_YEAR
) -> DiscoveryResult:
    """Загрузить все файлы из папки, определить вуз и собрать программы."""
    files = find_data_files(directory)
    result = DiscoveryResult()
    if not files:
        result.warnings.append(
            DataWarning(
                kind="empty_directory",
                severity="error",
                message=f"В папке «{directory}» нет файлов .tsv, .csv или .xlsx.",
            )
        )
        return result

    loaded: list[tuple[ProgramMetadata, list[Application], FileRecord]] = []
    for path in files:
        try:
            raw_table = read_table(path)
        except ReaderError as exc:
            result.warnings.append(DataWarning(kind="read_error", severity="error", message=str(exc)))
            result.files.append(
                FileRecord(
                    path=path, university_code=None, university_name=None, format_ok=False,
                    confidence=0.0, program_id=None, program_name=None, updated_at=None,
                    row_count=0, dropped=True, drop_reason=str(exc),
                )
            )
            continue

        metadata, applications, warnings, record = _load_one(raw_table, path, campaign_year)
        result.warnings.extend(warnings)
        result.files.append(record)
        if metadata is not None:
            loaded.append((metadata, applications, record))

    kept, version_warnings = _resolve_versions(loaded)
    result.warnings.extend(version_warnings)
    for program_id, (metadata, applications) in kept.items():
        result.programs[program_id] = metadata
        result.applications[program_id] = applications
    result.warnings = _dedupe_warnings(result.warnings)
    return result


def load_uploads(
    uploads: Iterable[Any], campaign_year: int = DEFAULT_CAMPAIGN_YEAR
) -> DiscoveryResult:
    """Загрузить из file-like объектов Streamlit (`.name`, `.read()`)."""
    result = DiscoveryResult()
    loaded: list[tuple[ProgramMetadata, list[Application], FileRecord]] = []
    for upload in uploads:
        path = Path(upload.name)
        try:
            raw_table = read_table_from_bytes(upload.read(), upload.name)
        except ReaderError as exc:
            result.warnings.append(DataWarning(kind="read_error", severity="error", message=str(exc)))
            continue
        metadata, applications, warnings, record = _load_one(raw_table, path, campaign_year)
        result.warnings.extend(warnings)
        result.files.append(record)
        if metadata is not None:
            loaded.append((metadata, applications, record))

    kept, version_warnings = _resolve_versions(loaded)
    result.warnings.extend(version_warnings)
    for program_id, (metadata, applications) in kept.items():
        result.programs[program_id] = metadata
        result.applications[program_id] = applications
    result.warnings = _dedupe_warnings(result.warnings)
    return result


# --------------------------------------------------------------------------
# Настройки программ (config/programs.json)
# --------------------------------------------------------------------------


def load_program_configs(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, ProgramConfig]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ProgramConfig] = {}
    for program_id, value in raw.items():
        if isinstance(value, dict):
            try:
                result[program_id] = ProgramConfig.from_dict(value)
            except TypeError:
                continue
    return result


def save_program_configs(
    configs: dict[str, ProgramConfig], path: str | Path = DEFAULT_CONFIG_PATH
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {pid: cfg.to_dict() for pid, cfg in sorted(configs.items())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_program_config(metadata: ProgramMetadata) -> ProgramConfig:
    """Конфиг по умолчанию — с местами из метаданных, если вуз их публикует."""
    return ProgramConfig(
        total_places=metadata.total_places or 0,
        funding_type=metadata.funding_type or "budget",
    )
