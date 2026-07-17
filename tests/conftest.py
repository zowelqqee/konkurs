import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from analyzer.models import (
    ApplicantKey,
    Application,
    CompetitionCategory,
    ProgramConfig,
    ProgramMetadata,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ITMO_FIXTURE = FIXTURES_DIR / "itmo" / "090303 тестовая программа.tsv"
HSE_FIXTURE = FIXTURES_DIR / "hse" / "hse_fixture.tsv"

CAMPAIGN_YEAR = 2026


def make_app(
    code: str,
    program_id: str,
    university_code: str = "itmo",
    priority: int = 1,
    total_score: int | None = None,
    category: CompetitionCategory = CompetitionCategory.GENERAL,
    bvi: bool = False,
    source_rank: int | None = None,
    consent: bool | None = True,
    campaign_year: int = CAMPAIGN_YEAR,
    active: bool = True,
    **kwargs,
) -> Application:
    """Компактный конструктор заявления для тестов распределения."""
    return Application(
        applicant_key=ApplicantKey(campaign_year=campaign_year, applicant_id=code),
        university_code=university_code,
        program_id=program_id,
        source_rank=source_rank,
        priority=priority,
        competition_category=category,
        bvi=bvi,
        total_score=total_score,
        exam_sum=kwargs.pop("exam_sum", total_score),
        consent=consent,
        active=active,
        **kwargs,
    )


def make_metadata(
    program_id: str, university_code: str = "itmo", name: str | None = None
) -> ProgramMetadata:
    university_name = {"itmo": "ИТМО", "hse": "НИУ ВШЭ"}.get(university_code, university_code)
    return ProgramMetadata(
        university_code=university_code,
        university_name=university_name,
        program_id=program_id,
        program_name=name or program_id,
        adapter_name=university_code,
    )


def make_config(total_places: int = 1, **kwargs) -> ProgramConfig:
    return ProgramConfig(total_places=total_places, **kwargs)


@pytest.fixture
def itmo_fixture_path() -> Path:
    return ITMO_FIXTURE


@pytest.fixture
def hse_fixture_path() -> Path:
    return HSE_FIXTURE
