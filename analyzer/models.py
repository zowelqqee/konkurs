"""Доменные модели мультивузового анализатора конкурсных списков.

Центральная идея: приоритет зачисления — понятие внутри одного вуза, а
уникальный код абитуриента (Госуслуги) — глобальный признак человека в рамках
одной приёмной кампании (`campaign_year`). Эти две системы координат нельзя
смешивать: приоритет 1 в одном вузе не выше и не ниже приоритета 2 в другом —
они просто не сравнимы.

БВИ здесь — не отдельная категория конкурса, а флаг (`Application.bvi`),
ортогональный категории. В данных ИТМО БВИ исторически шли отдельным пунктом
списка и потребляли места внутри общего бюджета; в данных ВШЭ БВИ — это право,
которое может сочетаться с любой конкурсной группой. Флаг позволяет обрабатывать
оба случая одним алгоритмом: БВИ ранжируются по исходному месту в списке и
рассматриваются раньше остальных конкурсов программы.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class CompetitionCategory(enum.Enum):
    """Категория конкурса, приведённая к единому виду для всех вузов."""

    SPECIAL_QUOTA = "особая квота"
    SEPARATE_QUOTA = "отдельная квота"
    TARGET_QUOTA = "целевая квота"
    GENERAL = "общий конкурс"
    PAID = "платные места"
    UNKNOWN = "неизвестная категория"

    @property
    def is_quota(self) -> bool:
        return self in _QUOTA_CATEGORIES

    @property
    def is_budget(self) -> bool:
        """Категория участвует в распределении бюджетных мест."""
        return self in _BUDGET_CATEGORIES


_QUOTA_CATEGORIES = frozenset(
    {
        CompetitionCategory.SPECIAL_QUOTA,
        CompetitionCategory.SEPARATE_QUOTA,
        CompetitionCategory.TARGET_QUOTA,
    }
)
_BUDGET_CATEGORIES = frozenset(
    {
        CompetitionCategory.SPECIAL_QUOTA,
        CompetitionCategory.SEPARATE_QUOTA,
        CompetitionCategory.TARGET_QUOTA,
        CompetitionCategory.GENERAL,
    }
)

# Порядок разбора конкурсов внутри программы: БВИ (обрабатывается отдельно от
# перечисления, см. allocation.py) -> квоты -> общий конкурс.
CATEGORY_TRACK_ORDER: tuple[CompetitionCategory, ...] = (
    CompetitionCategory.SPECIAL_QUOTA,
    CompetitionCategory.SEPARATE_QUOTA,
    CompetitionCategory.TARGET_QUOTA,
    CompetitionCategory.GENERAL,
)


class Status(enum.Enum):
    """Итоговый статус абитуриента на конкретной программе внутри вуза."""

    ALLOCATED = "проходит на эту программу"
    ALLOCATED_HIGHER = "проходит на программу с более высоким приоритетом"
    NOT_ALLOCATED = "не проходит"
    NO_CONSENT = "не участвует из-за отсутствия согласия"
    BVI = "БВИ"
    QUOTA = "проходит по квоте"
    INACTIVE = "заявление неактивно"
    NO_DATA = "данных недостаточно"


@dataclass(frozen=True)
class ApplicantKey:
    """Глобальный идентификатор человека в рамках одной приёмной кампании.

    Вуз намеренно не входит в ключ: один и тот же код Госуслуг у разных вузов —
    один и тот же человек. Один и тот же код в разных годах кампании — разные
    люди (номера не переиспользуются в течение кампании, но переиспользование
    между годами не гарантировано).
    """

    campaign_year: int
    applicant_id: str

    def __str__(self) -> str:  # удобный ключ для UI и JSON
        return f"{self.campaign_year}:{self.applicant_id}"


@dataclass
class ProgramMetadata:
    """Метаданные образовательной программы одного вуза."""

    university_code: str
    university_name: str

    program_id: str
    program_name: str

    education_level: Optional[str] = None
    study_form: Optional[str] = None
    campus: Optional[str] = None
    funding_type: Optional[str] = None

    competition_group: Optional[str] = None
    field_code: Optional[str] = None
    field_name: Optional[str] = None

    total_places: Optional[int] = None
    special_quota_places: Optional[int] = None
    separate_quota_places: Optional[int] = None
    target_quota_places: Optional[int] = None

    source_file: str = ""
    updated_at: Optional[datetime] = None
    adapter_name: str = ""

    original_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        prefix = f"{self.field_code} " if self.field_code else ""
        return f"{prefix}{self.program_name}"

    def quota_sum(self) -> int:
        return (
            (self.special_quota_places or 0)
            + (self.separate_quota_places or 0)
            + (self.target_quota_places or 0)
        )


@dataclass
class Application:
    """Одна строка конкурсного списка после нормализации адаптером вуза."""

    applicant_key: ApplicantKey

    university_code: str
    program_id: str

    source_rank: Optional[int] = None
    priority: Optional[int] = None

    competition_category: CompetitionCategory = CompetitionCategory.UNKNOWN

    bvi: bool = False
    bvi_reason: Optional[str] = None

    exam_1: Optional[int] = None
    exam_2: Optional[int] = None
    exam_3: Optional[int] = None
    exam_sum: Optional[int] = None
    individual_score: Optional[int] = None
    total_score: Optional[int] = None

    consent: Optional[bool] = None

    active: bool = True
    application_status: Optional[str] = None
    documents_withdrawn: Optional[bool] = None
    all_exams_passed: Optional[bool] = None

    preferential_right: Optional[bool] = None
    preferential_right_9: Optional[bool] = None
    preferential_right_10: Optional[bool] = None

    original_values: dict[str, Any] = field(default_factory=dict)
    normalization_notes: list[str] = field(default_factory=list)

    @property
    def code(self) -> str:
        """Код абитуриента без года кампании — для отображения и tie-break."""
        return self.applicant_key.applicant_id

    def sort_score(self) -> int:
        return self.total_score if self.total_score is not None else -1


@dataclass
class ApplicantProfile:
    """Все заявления одного человека, объединённые по глобальному коду."""

    applicant_key: ApplicantKey
    applications: list[Application] = field(default_factory=list)
    universities: set[str] = field(default_factory=set)


@dataclass
class DetectionResult:
    """Результат попытки адаптера распознать формат файла."""

    matched: bool
    confidence: float
    adapter_name: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DataWarning:
    """Предупреждение проверки данных."""

    kind: str
    message: str
    university_code: Optional[str] = None
    program_id: Optional[str] = None
    applicant_id: Optional[str] = None
    severity: str = "warning"  # "info" | "warning" | "error"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProgramConfig:
    """Настройки распределения по программе. Задаются пользователем вручную."""

    total_places: int = 0
    funding_type: str = "budget"
    special_quota_places: int = 0
    separate_quota_places: int = 0
    target_quota_places: int = 0
    bvi_within_budget: bool = True
    redistribute_unfilled_quota: bool = True
    include_under_review: bool = False
    consent_only: bool = False
    max_priority: Optional[int] = None

    def quota_sum(self) -> int:
        return self.special_quota_places + self.separate_quota_places + self.target_quota_places

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "ProgramConfig":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class ScenarioConfig:
    """Настройки внутривузовского сценария распределения."""

    name: str = "custom"
    respect_consent: bool = False
    count_bvi: bool = True
    use_quotas: bool = True
    redistribute_unfilled_quota: bool = True
    include_under_review: bool = False
    max_priority: Optional[int] = None
    borderline_window: int = 5

    def to_dict(self) -> dict:
        return asdict(self)


INTRA_SCENARIOS: dict[str, ScenarioConfig] = {
    "strict_current": ScenarioConfig(name="strict_current", respect_consent=True),
    "highest_priority": ScenarioConfig(name="highest_priority", respect_consent=False),
    "first_priority": ScenarioConfig(name="first_priority", respect_consent=False, max_priority=1),
    "custom": ScenarioConfig(name="custom"),
}

INTRA_SCENARIO_LABELS = {
    "strict_current": "Строгий текущий (только согласие = да)",
    "highest_priority": "По высшему приоритету (все заявления)",
    "first_priority": "Первый приоритет (только приоритет 1)",
    "custom": "Пользовательский",
}

# Межвузовские сценарии. `user_preferences` из ТЗ реализован как
# `preference_based` — см. README, раздел «Расхождения в спецификации».
CROSS_SCENARIOS = ("independent", "consent_based", "preference_based", "probabilistic")

CROSS_SCENARIO_LABELS = {
    "independent": "Независимый (показать все вузы, где проходит)",
    "consent_based": "По согласию",
    "preference_based": "По заданным предпочтениям вузов",
    "probabilistic": "Вероятностный (Monte Carlo, экспериментальный)",
}


@dataclass
class Seat:
    """Занятое место: результат внутривузового распределения одного человека."""

    applicant_key: ApplicantKey
    program_id: str
    category: CompetitionCategory
    bvi: bool
    priority: Optional[int]
    rank: int
    application: Application


@dataclass
class ProgramResult:
    """Результат внутривузового распределения по одной программе."""

    program_id: str
    university_code: str
    title: str
    seats_total: int
    bvi_count: int = 0
    quota_filled: dict[str, int] = field(default_factory=dict)
    general_seats: int = 0
    allocated: list[Seat] = field(default_factory=list)
    passing_score: Optional[int] = None
    passing_score_no_individual: Optional[int] = None
    last_admitted_id: Optional[str] = None
    first_rejected_id: Optional[str] = None
    first_rejected_score: Optional[int] = None
    applications_count: int = 0
    unique_applicants: int = 0
    left_for_higher: int = 0
    free_seats: int = 0

    @property
    def allocated_count(self) -> int:
        return len(self.allocated)


@dataclass
class IntraUniversityResult:
    """Результат внутривузового распределения — для одного вуза."""

    university_code: str
    programs: dict[str, ProgramResult] = field(default_factory=dict)
    assignment: dict[ApplicantKey, str] = field(default_factory=dict)
    rankings: dict[tuple, list[Application]] = field(default_factory=dict)
    iterations: int = 0
    converged: bool = True
    warnings: list[DataWarning] = field(default_factory=list)
    scenario: ScenarioConfig = field(default_factory=ScenarioConfig)


@dataclass
class UniversityOffer:
    """Предложение одного вуза конкретному абитуриенту."""

    university_code: str
    program_id: str
    priority: Optional[int]
    status: str
    consent: Optional[bool]
    total_score: Optional[int]


@dataclass
class CrossUniversityResult:
    """Межвузовский результат по одному абитуриенту."""

    applicant_key: ApplicantKey
    offers: list[UniversityOffer] = field(default_factory=list)
    selected_offer: Optional[UniversityOffer] = None
    selection_reason: str = ""
    ambiguous: bool = False


@dataclass
class FileRecord:
    """Одна строка таблицы «найденные файлы» в интерфейсе."""

    path: Path
    university_code: Optional[str]
    university_name: Optional[str]
    format_ok: bool
    confidence: float
    program_id: Optional[str]
    program_name: Optional[str]
    updated_at: Optional[datetime]
    row_count: int
    dropped: bool = False
    drop_reason: str = ""
