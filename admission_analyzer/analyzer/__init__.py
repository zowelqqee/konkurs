"""Мультивузовый анализ конкурсных списков с учётом приоритетов.

Вся логика работает без интерфейса:

    from analyzer import discovery, allocation, cross_university

    result = discovery.load_directory("data", campaign_year=2026)
    configs = {pid: discovery.default_program_config(m) for pid, m in result.programs.items()}
    intra = allocation.allocate_all(result.programs, configs, result.applications, SCENARIOS["highest_priority"])
    profiles = cross_university.build_applicant_profiles(result.applications)
    cross = cross_university.resolve_all(profiles, intra, "consent_based")
"""

from . import allocation, cross_university, discovery, reports, validation
from .models import (
    ApplicantKey,
    ApplicantProfile,
    Application,
    CATEGORY_TRACK_ORDER,
    CompetitionCategory,
    CROSS_SCENARIOS,
    CROSS_SCENARIO_LABELS,
    CrossUniversityResult,
    DataWarning,
    DetectionResult,
    FileRecord,
    INTRA_SCENARIOS,
    INTRA_SCENARIO_LABELS,
    IntraUniversityResult,
    ProgramConfig,
    ProgramMetadata,
    ProgramResult,
    ScenarioConfig,
    Seat,
    Status,
    UniversityOffer,
)
from .readers import ReaderError
from .universities import ADAPTERS, HSEAdapter, ITMOAdapter, UniversityAdapter

__all__ = [
    "ADAPTERS",
    "ApplicantKey",
    "ApplicantProfile",
    "Application",
    "CATEGORY_TRACK_ORDER",
    "CROSS_SCENARIOS",
    "CROSS_SCENARIO_LABELS",
    "CompetitionCategory",
    "CrossUniversityResult",
    "DataWarning",
    "DetectionResult",
    "FileRecord",
    "HSEAdapter",
    "INTRA_SCENARIOS",
    "INTRA_SCENARIO_LABELS",
    "ITMOAdapter",
    "IntraUniversityResult",
    "ProgramConfig",
    "ProgramMetadata",
    "ProgramResult",
    "ReaderError",
    "ScenarioConfig",
    "Seat",
    "Status",
    "UniversityAdapter",
    "UniversityOffer",
    "allocation",
    "cross_university",
    "discovery",
    "reports",
    "validation",
]
