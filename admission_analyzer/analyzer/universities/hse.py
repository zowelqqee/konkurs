"""Адаптер НИУ ВШЭ: уникальный идентификатор + приоритет зачисления + статусы."""

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
    first_non_empty,
    header_key,
    normalize_bool,
    normalize_code,
    normalize_dash_bool,
    normalize_int,
    normalize_text,
    slugify,
)
from ..readers import RawTable
from .base import UniversityAdapter

F_RANK = "rank"
F_CODE = "code"
F_BVI = "bvi"
F_BVI_REASON = "bvi_reason"
F_PRIORITY = "priority"
F_PRIORITY_PAID = "priority_paid"
F_CONSENT = "consent"
F_EXAM_1 = "exam_1"
F_EXAM_2 = "exam_2"
F_EXAM_3 = "exam_3"
F_EXAM_SUM = "exam_sum"
F_INDIVIDUAL = "individual_score"
F_TOTAL = "total_score"
F_ALL_PASSED = "all_exams_passed"
F_STATUS = "status"
F_PREF_9 = "preferential_9"
F_PREF_10 = "preferential_10"
F_DORM = "dormitory"
F_WITHDRAWN = "withdrawn"

REQUIRED_FIELDS = (F_CODE, F_CONSENT, F_TOTAL)

HEADER_ALIASES: dict[str, str] = {
    "n п п": F_RANK,
    "no п п": F_RANK,
    "место": F_RANK,
    "уникальный идентификатор абитуриента": F_CODE,
    "уникальный идентификатор": F_CODE,
    "право поступления без вступительных испытаний": F_BVI,
    "основание бви": F_BVI_REASON,
    "приоритет зачисления": F_PRIORITY,
    "приоритет платных мест": F_PRIORITY_PAID,
    "наличие согласия на зачисление": F_CONSENT,
    "ви 1": F_EXAM_1,
    "ви1": F_EXAM_1,
    "ви 2": F_EXAM_2,
    "ви2": F_EXAM_2,
    "ви 3": F_EXAM_3,
    "ви3": F_EXAM_3,
    "сумма баллов за вступительные испытания": F_EXAM_SUM,
    "сумма баллов по индивидуальным достижениям": F_INDIVIDUAL,
    "сумма конкурсных баллов": F_TOTAL,
    "все оценки положительные": F_ALL_PASSED,
    "статус заявления": F_STATUS,
    "преимущественное право п 9": F_PREF_9,
    "преимущественное право п9": F_PREF_9,
    "преимущественное право п 10": F_PREF_10,
    "преимущественное право п10": F_PREF_10,
    "требуется общежитие на время обучения": F_DORM,
    "возврат документов": F_WITHDRAWN,
}

# Метаданные программы, дублируемые в каждой строке.
META_PROGRAM_NAME = ("образовательная программа",)
META_PLACES = ("количество мест",)
META_UPDATED_AT = ("дата обновления",)
META_FIELD_NAME = ("направление подготовки",)
META_COMPETITION_GROUP = ("конкурсная группа",)
META_CAMPUS = ("филиал", "кампус")
META_STUDY_FORM = ("форма обучения",)
META_FUNDING = ("вид места", "основа обучения", "уровень финансирования")

STATUS_WITHDRAWN_BY_APPLICANT = "отозвано поступающим"
STATUS_UNDER_REVIEW = "на рассмотрении"
STATUS_ACTIVE = "участвует в конкурсе"

SIGNATURE_FIELDS = (F_CODE, F_PRIORITY, F_CONSENT, F_TOTAL, F_ALL_PASSED, F_STATUS, F_BVI)


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


def _find_meta_column(columns: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    for column in columns:
        key = header_key(column)
        if key in candidates:
            return column
    return None


class HSEAdapter(UniversityAdapter):
    university_code = "hse"
    university_name = "НИУ ВШЭ"

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
                "похоже на формат ВШЭ, но не хватает обязательных колонок: "
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
        df = raw_table.dataframe
        columns = list(df.columns)

        def meta(candidates: tuple[str, ...]) -> Optional[str]:
            column = _find_meta_column(columns, candidates)
            if column is None:
                return None
            return first_non_empty(list(df[column]))

        program_name = meta(META_PROGRAM_NAME) or file_path.stem
        places_text = meta(META_PLACES)
        updated_text = meta(META_UPDATED_AT)
        field_name = meta(META_FIELD_NAME)
        competition_group = meta(META_COMPETITION_GROUP)
        campus = meta(META_CAMPUS)
        study_form = meta(META_STUDY_FORM)
        funding_type = meta(META_FUNDING) or "budget"

        updated_at = None
        if updated_text:
            updated_at = _parse_datetime(updated_text)

        segments = [slugify(program_name)]
        if campus:
            segments.append(slugify(campus))
        elif study_form:
            segments.append(slugify(study_form))
        segments.append(slugify(funding_type))
        program_id = "hse:" + ":".join(segments)

        return ProgramMetadata(
            university_code=self.university_code,
            university_name=self.university_name,
            program_id=program_id,
            program_name=program_name,
            education_level="бакалавриат",
            study_form=study_form,
            campus=campus,
            funding_type=funding_type,
            competition_group=competition_group,
            field_name=field_name,
            total_places=normalize_int(places_text),
            source_file=file_path.name,
            updated_at=updated_at,
            adapter_name=self.university_code,
            original_metadata={
                "образовательная программа": program_name,
                "количество мест": places_text,
                "дата обновления": updated_text,
            },
        )

    def normalize_applications(
        self, raw_table: RawTable, metadata: ProgramMetadata
    ) -> list[Application]:
        df = raw_table.dataframe
        if df.empty:
            return []
        mapping = self._map_columns(raw_table)
        if F_CODE not in mapping:
            return []

        def cell(row: Any, field_name: str) -> Any:
            column = mapping.get(field_name)
            return row.get(column) if column else None

        applications: list[Application] = []
        for _, row in df.iterrows():
            code = normalize_code(cell(row, F_CODE))
            if code is None:
                continue

            bvi = bool(normalize_bool(cell(row, F_BVI)))
            bvi_reason = normalize_text(cell(row, F_BVI_REASON))

            priority = normalize_int(cell(row, F_PRIORITY))
            if priority is None:
                priority = normalize_int(cell(row, F_PRIORITY_PAID))

            consent = normalize_dash_bool(cell(row, F_CONSENT)) if F_CONSENT in mapping else None

            status = normalize_text(cell(row, F_STATUS))
            status_key = header_key(status) if status else None
            all_passed = normalize_bool(cell(row, F_ALL_PASSED))
            withdrawn = normalize_bool(cell(row, F_WITHDRAWN))
            documents_withdrawn = bool(withdrawn) if withdrawn is not None else None

            active = True
            if status_key == STATUS_WITHDRAWN_BY_APPLICANT:
                active = False
            if documents_withdrawn:
                active = False
            if all_passed is False:
                active = False

            pref_9 = normalize_bool(cell(row, F_PREF_9))
            pref_10 = normalize_bool(cell(row, F_PREF_10))
            preferential_right = None
            if pref_9 is not None or pref_10 is not None:
                preferential_right = bool(pref_9) or bool(pref_10)

            original_values = {
                str(col): row.get(col) for col in df.columns if col in mapping.values()
            }

            applications.append(
                Application(
                    applicant_key=ApplicantKey(campaign_year=0, applicant_id=code),
                    university_code=self.university_code,
                    program_id=metadata.program_id,
                    source_rank=normalize_int(cell(row, F_RANK)),
                    priority=priority,
                    competition_category=CompetitionCategory.GENERAL,
                    bvi=bvi,
                    bvi_reason=bvi_reason,
                    exam_1=normalize_int(cell(row, F_EXAM_1)),
                    exam_2=normalize_int(cell(row, F_EXAM_2)),
                    exam_3=normalize_int(cell(row, F_EXAM_3)),
                    exam_sum=normalize_int(cell(row, F_EXAM_SUM)),
                    individual_score=normalize_int(cell(row, F_INDIVIDUAL)),
                    total_score=normalize_int(cell(row, F_TOTAL)),
                    consent=consent,
                    active=active,
                    application_status=status,
                    documents_withdrawn=documents_withdrawn,
                    all_exams_passed=all_passed,
                    preferential_right=preferential_right,
                    preferential_right_9=pref_9,
                    preferential_right_10=pref_10,
                    original_values=original_values,
                    normalization_notes=(
                        [f"статус «{status}» сохранён, требует настройки include_under_review"]
                        if status_key == STATUS_UNDER_REVIEW
                        else []
                    ),
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
                        message=f"«{metadata.title}», код {app.code}: нет приоритета (ни зачисления, ни платного).",
                    )
                )
            if app.total_score is not None and not 0 <= app.total_score <= 400:
                warnings.append(
                    DataWarning(
                        kind="bad_score",
                        university_code=self.university_code,
                        program_id=metadata.program_id,
                        applicant_id=app.code,
                        message=f"«{metadata.title}», код {app.code}: сумма конкурсных баллов {app.total_score} вне диапазона.",
                    )
                )
            if app.documents_withdrawn:
                warnings.append(
                    DataWarning(
                        kind="documents_withdrawn",
                        severity="info",
                        university_code=self.university_code,
                        program_id=metadata.program_id,
                        applicant_id=app.code,
                        message=f"«{metadata.title}», код {app.code}: возврат документов.",
                    )
                )
            if app.all_exams_passed is False:
                warnings.append(
                    DataWarning(
                        kind="non_positive_scores",
                        severity="info",
                        university_code=self.university_code,
                        program_id=metadata.program_id,
                        applicant_id=app.code,
                        message=f"«{metadata.title}», код {app.code}: не все оценки положительные, заявление не участвует.",
                    )
                )
        return warnings


def _parse_datetime(text: str):
    from datetime import datetime

    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None
