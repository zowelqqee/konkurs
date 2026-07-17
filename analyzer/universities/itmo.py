"""Адаптер ИТМО: категория/место/код/приоритет + баллы ЕГЭ/ВИ."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..models import (
    Application,
    ApplicantKey,
    CompetitionCategory,
    DataWarning,
    DetectionResult,
    ProgramMetadata,
)
from ..normalization import (
    forward_fill,
    header_key,
    normalize_bool,
    normalize_code,
    normalize_int,
    normalize_text,
    slugify,
)
from ..readers import RawTable
from .base import UniversityAdapter

# Канонические поля.
F_CATEGORY = "category"
F_PLACE = "place"
F_CODE = "code"
F_PRIORITY = "priority"
F_EXAM_KIND = "exam_kind"
F_MATH = "math"
F_INFORMATICS = "informatics"
F_RUSSIAN = "russian"
F_ACHIEVEMENTS = "achievements"
F_SCORE_VI = "score_vi"
F_SCORE_TOTAL = "score_total"
F_PREFERENTIAL = "preferential_right"
F_MAIN_HIGHEST = "main_highest_priority"
F_HIGHEST_PASSING = "highest_passing_priority"
F_CONSENT = "consent"
F_OLYMPIAD = "olympiad"

REQUIRED_FIELDS = (F_CODE, F_PRIORITY, F_CATEGORY)

HEADER_ALIASES: dict[str, str] = {
    "категория": F_CATEGORY,
    "категория конкурса": F_CATEGORY,
    "вид конкурса": F_CATEGORY,
    "конкурсная группа": F_CATEGORY,
    "место": F_PLACE,
    "номер": F_PLACE,
    "n": F_PLACE,
    "код": F_CODE,
    "код абитуриента": F_CODE,
    "уникальный код": F_CODE,
    "уникальный идентификатор": F_CODE,
    "уникальный идентификатор абитуриента": F_CODE,
    "id": F_CODE,
    "снилс": F_CODE,
    "приоритет": F_PRIORITY,
    "приоритет зачисления": F_PRIORITY,
    "прио": F_PRIORITY,
    "вид испытания": F_EXAM_KIND,
    "вид испытаний": F_EXAM_KIND,
    "тип испытания": F_EXAM_KIND,
    "математика": F_MATH,
    "матем": F_MATH,
    "мат": F_MATH,
    "информатика": F_INFORMATICS,
    "информ": F_INFORMATICS,
    "икт": F_INFORMATICS,
    "информатика и икт": F_INFORMATICS,
    "русский язык": F_RUSSIAN,
    "русский": F_RUSSIAN,
    "рус": F_RUSSIAN,
    "ид": F_ACHIEVEMENTS,
    "индивидуальные достижения": F_ACHIEVEMENTS,
    "балл ид": F_ACHIEVEMENTS,
    "балл ви": F_SCORE_VI,
    "баллы ви": F_SCORE_VI,
    "сумма ви": F_SCORE_VI,
    "сумма баллов ви": F_SCORE_VI,
    "балл ви+ид": F_SCORE_TOTAL,
    "балл ви + ид": F_SCORE_TOTAL,
    "баллы ви+ид": F_SCORE_TOTAL,
    "сумма": F_SCORE_TOTAL,
    "сумма баллов": F_SCORE_TOTAL,
    "итоговый балл": F_SCORE_TOTAL,
    "преимущественное право": F_PREFERENTIAL,
    "преим право": F_PREFERENTIAL,
    "пп": F_PREFERENTIAL,
    "основной высший приоритет": F_MAIN_HIGHEST,
    "высший проходной приоритет": F_HIGHEST_PASSING,
    "согласие": F_CONSENT,
    "есть согласие": F_CONSENT,
    "согласие на зачисление": F_CONSENT,
    "наличие согласия на зачисление": F_CONSENT,
    "оригинал": F_CONSENT,
    "оригинал документа": F_CONSENT,
    "олимпиада": F_OLYMPIAD,
    "олимпиады": F_OLYMPIAD,
}

CATEGORY_ALIASES: dict[str, tuple[CompetitionCategory, bool]] = {
    # значение -> (категория, признак БВИ)
    "без вступительных испытаний": (CompetitionCategory.GENERAL, True),
    "без ви": (CompetitionCategory.GENERAL, True),
    "бви": (CompetitionCategory.GENERAL, True),
    "победители и призеры олимпиад": (CompetitionCategory.GENERAL, True),
    "особая квота": (CompetitionCategory.SPECIAL_QUOTA, False),
    "особая": (CompetitionCategory.SPECIAL_QUOTA, False),
    "квота лиц с особыми правами": (CompetitionCategory.SPECIAL_QUOTA, False),
    "отдельная квота": (CompetitionCategory.SEPARATE_QUOTA, False),
    "отдельная": (CompetitionCategory.SEPARATE_QUOTA, False),
    "специальная квота": (CompetitionCategory.SEPARATE_QUOTA, False),
    "целевая квота": (CompetitionCategory.TARGET_QUOTA, False),
    "целевая": (CompetitionCategory.TARGET_QUOTA, False),
    "целевой прием": (CompetitionCategory.TARGET_QUOTA, False),
    "общий конкурс": (CompetitionCategory.GENERAL, False),
    "общие места": (CompetitionCategory.GENERAL, False),
    "основные места": (CompetitionCategory.GENERAL, False),
    "общий": (CompetitionCategory.GENERAL, False),
    "на общих основаниях": (CompetitionCategory.GENERAL, False),
    "платные места": (CompetitionCategory.PAID, False),
    "платный конкурс": (CompetitionCategory.PAID, False),
    "платное": (CompetitionCategory.PAID, False),
    "договор": (CompetitionCategory.PAID, False),
    "контракт": (CompetitionCategory.PAID, False),
}

# Заголовки, которые почти наверняка встречаются только в файлах ИТМО —
# используются для оценки confidence при определении формата.
SIGNATURE_FIELDS = (F_CATEGORY, F_PLACE, F_CODE, F_PRIORITY, F_SCORE_TOTAL, F_CONSENT)


def _normalize_header(value: Any) -> Optional[str]:
    key = header_key(value)
    if not key:
        return None
    if key in HEADER_ALIASES:
        return HEADER_ALIASES[key]
    compact = key.replace(" ", "")
    for alias, field_name in HEADER_ALIASES.items():
        if alias.replace(" ", "") == compact:
            return field_name
    return None


def _normalize_category(value: Any) -> Optional[tuple[CompetitionCategory, bool]]:
    key = header_key(value)
    if not key:
        return None
    if key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[key]
    for alias, mapped in CATEGORY_ALIASES.items():
        if key.startswith(alias) or alias in key:
            return mapped
    return (CompetitionCategory.UNKNOWN, False)


class ITMOAdapter(UniversityAdapter):
    university_code = "itmo"
    university_name = "ИТМО"

    def _map_columns(self, raw_table: RawTable) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for column in raw_table.dataframe.columns:
            field_name = _normalize_header(column)
            if field_name and field_name not in mapping:
                mapping[field_name] = column
        return mapping

    def detect(self, raw_table: RawTable, file_path: Path) -> DetectionResult:
        if raw_table.is_empty:
            return DetectionResult(
                matched=False, confidence=0.0, adapter_name=self.university_code,
                reasons=["файл пуст"],
            )
        mapping = self._map_columns(raw_table)
        found = [f for f in SIGNATURE_FIELDS if f in mapping]
        confidence = len(found) / len(SIGNATURE_FIELDS)
        missing_required = [f for f in REQUIRED_FIELDS if f not in mapping]
        reasons = [f"найдена колонка «{mapping[f]}»" for f in found]
        matched = confidence >= 0.5 and not missing_required
        warnings = []
        if missing_required and confidence > 0:
            warnings.append(
                "похоже на формат ИТМО, но не хватает обязательных колонок: "
                + ", ".join(missing_required)
            )
        return DetectionResult(
            matched=matched,
            confidence=confidence,
            adapter_name=self.university_code,
            reasons=reasons,
            warnings=warnings,
        )

    def parse_program_metadata(self, raw_table: RawTable, file_path: Path) -> ProgramMetadata:
        import re

        stem = file_path.stem
        match = re.search(r"(?<!\d)(\d{2})[.\-\s]?(\d{2})[.\-\s]?(\d{2})(?!\d)", stem)
        field_code = ".".join(match.groups()) if match else None
        name = stem
        if match:
            name = (stem[: match.start()] + stem[match.end():]).strip()
        name = re.sub(r"\s+", " ", name).strip(" -_.") or stem

        program_id = f"itmo:{field_code or slugify(name)}:{slugify(name)}:budget"
        return ProgramMetadata(
            university_code=self.university_code,
            university_name=self.university_name,
            program_id=program_id,
            program_name=name,
            education_level="бакалавриат",
            funding_type="budget",
            field_code=field_code,
            field_name=name,
            source_file=file_path.name,
            adapter_name=self.university_code,
            original_metadata={},
        )

    def normalize_applications(
        self, raw_table: RawTable, metadata: ProgramMetadata
    ) -> list[Application]:
        df = raw_table.dataframe
        if df.empty:
            return []
        mapping = self._map_columns(raw_table)
        if F_CATEGORY not in mapping:
            return []

        raw_categories = forward_fill(
            list(df[mapping[F_CATEGORY]]), lambda v: _normalize_category(v)
        )

        def cell(row: Any, field_name: str) -> Any:
            column = mapping.get(field_name)
            return row.get(column) if column else None

        applications: list[Application] = []
        for offset, (_, row) in enumerate(df.iterrows()):
            code = normalize_code(cell(row, F_CODE))
            if code is None:
                continue
            category, bvi = raw_categories[offset] or (CompetitionCategory.UNKNOWN, False)
            consent = normalize_bool(cell(row, F_CONSENT)) if F_CONSENT in mapping else None
            olympiad = normalize_text(cell(row, F_OLYMPIAD))
            original_values = {
                str(col): row.get(col) for col in df.columns if col in mapping.values()
            }
            applications.append(
                Application(
                    applicant_key=ApplicantKey(campaign_year=0, applicant_id=code),  # год проставит discovery
                    university_code=self.university_code,
                    program_id=metadata.program_id,
                    source_rank=normalize_int(cell(row, F_PLACE)),
                    priority=normalize_int(cell(row, F_PRIORITY)),
                    competition_category=category,
                    bvi=bvi,
                    bvi_reason=olympiad if bvi else None,
                    exam_1=normalize_int(cell(row, F_MATH)),
                    exam_2=normalize_int(cell(row, F_INFORMATICS)),
                    exam_3=normalize_int(cell(row, F_RUSSIAN)),
                    exam_sum=normalize_int(cell(row, F_SCORE_VI)),
                    individual_score=normalize_int(cell(row, F_ACHIEVEMENTS)),
                    total_score=normalize_int(cell(row, F_SCORE_TOTAL)),
                    consent=consent,
                    active=True,
                    preferential_right=normalize_bool(cell(row, F_PREFERENTIAL)),
                    original_values=original_values,
                    normalization_notes=[],
                )
            )
        return applications

    def validate(
        self, applications: list[Application], metadata: ProgramMetadata
    ) -> list[DataWarning]:
        warnings: list[DataWarning] = []
        for app in applications:
            if app.priority is None:
                warnings.append(
                    DataWarning(
                        kind="missing_priority",
                        university_code=self.university_code,
                        program_id=metadata.program_id,
                        applicant_id=app.code,
                        message=f"«{metadata.title}», код {app.code}: нет приоритета.",
                    )
                )
            if app.competition_category is CompetitionCategory.UNKNOWN:
                warnings.append(
                    DataWarning(
                        kind="unknown_category",
                        university_code=self.university_code,
                        program_id=metadata.program_id,
                        applicant_id=app.code,
                        message=f"«{metadata.title}», код {app.code}: неизвестная категория конкурса.",
                    )
                )
            for name, value in (("Математика", app.exam_1), ("Информатика", app.exam_2), ("Русский язык", app.exam_3)):
                if value is not None and not 0 <= value <= 100:
                    warnings.append(
                        DataWarning(
                            kind="bad_score",
                            university_code=self.university_code,
                            program_id=metadata.program_id,
                            applicant_id=app.code,
                            message=f"«{metadata.title}», код {app.code}: {name} = {value} вне диапазона 0-100.",
                        )
                    )
        return warnings
