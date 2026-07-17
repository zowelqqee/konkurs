"""Проверки данных: файлов, программ, настроек и результатов распределения.

Часть проверок (пропущенный приоритет, некорректный балл, возврат документов и
т.п.) выполняется адаптером вуза прямо при загрузке (`UniversityAdapter.validate`)
и попадает в `DiscoveryResult.warnings`. Здесь — проверки, которые требуют
взгляда поверх одного файла: дубли внутри программы, межвузовские совпадения
кода, настройки мест, согласованность результата распределения.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .discovery import DiscoveryResult
from .models import (
    ApplicantProfile,
    Application,
    CompetitionCategory,
    DataWarning,
    IntraUniversityResult,
    ProgramConfig,
    ProgramMetadata,
)


def validate_discovery(
    discovery: DiscoveryResult, campaign_year: int
) -> list[DataWarning]:
    """Проверки на уровне загруженных программ и заявлений."""
    warnings: list[DataWarning] = []

    for program_id, applications in discovery.applications.items():
        metadata = discovery.programs[program_id]
        if not applications:
            warnings.append(
                DataWarning(
                    kind="empty_program",
                    program_id=program_id,
                    university_code=metadata.university_code,
                    message=f"«{metadata.title}» ({metadata.university_name}): нет строк с абитуриентами.",
                )
            )
            continue

        per_category: dict[CompetitionCategory, Counter] = defaultdict(Counter)
        for app in applications:
            per_category[app.competition_category][app.code] += 1
        for category, counter in per_category.items():
            dups = [c for c, n in counter.items() if n > 1]
            if dups:
                warnings.append(
                    DataWarning(
                        kind="duplicate_code_in_program",
                        program_id=program_id,
                        university_code=metadata.university_code,
                        message=(
                            f"«{metadata.title}», категория «{category.value}»: "
                            f"{len(dups)} кодов встречаются несколько раз "
                            f"(например {', '.join(sorted(dups)[:5])})."
                        ),
                    )
                )

        if metadata.updated_at is not None and metadata.updated_at.year not in (campaign_year, 0):
            warnings.append(
                DataWarning(
                    kind="campaign_year_mismatch",
                    severity="warning",
                    program_id=program_id,
                    university_code=metadata.university_code,
                    message=(
                        f"«{metadata.title}»: дата обновления файла ({metadata.updated_at.date()}) "
                        f"относится к другому году, а расчёт ведётся для кампании {campaign_year}."
                    ),
                )
            )

    warnings.extend(_duplicate_priority_within_university(discovery))
    warnings.extend(_cross_university_code_info(discovery))
    return warnings


def _duplicate_priority_within_university(discovery: DiscoveryResult) -> list[DataWarning]:
    """Одинаковый приоритет у одного человека на нескольких программах ОДНОГО вуза."""
    warnings: list[DataWarning] = []
    by_university: dict[str, dict[str, dict[int, set[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    for program_id, applications in discovery.applications.items():
        metadata = discovery.programs[program_id]
        for app in applications:
            if app.priority is None:
                continue
            by_university[metadata.university_code][app.code][app.priority].add(program_id)

    for university_code, per_code in by_university.items():
        for code, by_priority in per_code.items():
            for priority, program_ids in by_priority.items():
                if len(program_ids) > 1:
                    warnings.append(
                        DataWarning(
                            kind="duplicate_priority",
                            severity="warning",
                            university_code=university_code,
                            applicant_id=code,
                            message=(
                                f"Код {code} в {university_code}: приоритет {priority} указан сразу в "
                                f"{len(program_ids)} программах ({', '.join(sorted(program_ids))})."
                            ),
                        )
                    )
    return warnings


def _cross_university_code_info(discovery: DiscoveryResult) -> list[DataWarning]:
    """Один код в нескольких вузах — это норма, информационное сообщение."""
    code_to_universities: dict[str, set[str]] = defaultdict(set)
    for program_id, applications in discovery.applications.items():
        metadata = discovery.programs[program_id]
        for app in applications:
            code_to_universities[app.code].add(metadata.university_code)

    multi = {code: unis for code, unis in code_to_universities.items() if len(unis) > 1}
    if not multi:
        return []
    return [
        DataWarning(
            kind="cross_university_applicant",
            severity="info",
            message=(
                f"{len(multi)} кодов найдены в нескольких вузах одновременно — "
                "это ожидаемо для абитуриентов, подавших документы в разные вузы."
            ),
        )
    ]


def validate_configs(
    programs: dict[str, ProgramMetadata], configs: dict[str, ProgramConfig]
) -> list[DataWarning]:
    """Проверки настроек мест по программам."""
    warnings: list[DataWarning] = []
    for program_id, metadata in programs.items():
        config = configs.get(program_id)
        if config is None:
            continue
        if config.quota_sum() > config.total_places:
            warnings.append(
                DataWarning(
                    kind="quota_exceeds_places",
                    severity="error",
                    program_id=program_id,
                    university_code=metadata.university_code,
                    message=(
                        f"«{metadata.title}»: сумма квотных мест ({config.quota_sum()}) больше "
                        f"общего числа мест ({config.total_places})."
                    ),
                )
            )
    return warnings


def validate_bvi_capacity(
    programs: dict[str, ProgramMetadata],
    configs: dict[str, ProgramConfig],
    applications: dict[str, list[Application]],
) -> list[DataWarning]:
    warnings: list[DataWarning] = []
    for program_id, apps in applications.items():
        metadata = programs[program_id]
        config = configs.get(program_id)
        if config is None or not config.total_places:
            continue
        bvi_count = sum(1 for a in apps if a.bvi)
        if bvi_count > config.total_places:
            warnings.append(
                DataWarning(
                    kind="bvi_exceeds_places",
                    program_id=program_id,
                    university_code=metadata.university_code,
                    message=(
                        f"«{metadata.title}»: БВИ ({bvi_count}) больше числа мест "
                        f"({config.total_places})."
                    ),
                )
            )
    return warnings


def validate_intra_result(result: IntraUniversityResult) -> list[DataWarning]:
    """Проверки самого результата внутривузового распределения."""
    warnings: list[DataWarning] = []
    seen: dict[str, str] = {}
    for program_id, program_result in result.programs.items():
        for seat in program_result.allocated:
            code = seat.application.code
            if code in seen and seen[code] != program_id:
                warnings.append(
                    DataWarning(
                        kind="multiple_allocation",
                        severity="error",
                        university_code=result.university_code,
                        applicant_id=code,
                        message=(
                            f"Код {code} в {result.university_code} распределён сразу на "
                            f"«{seen[code]}» и «{program_id}» — ошибка алгоритма."
                        ),
                    )
                )
            seen[code] = program_id
        if program_result.allocated_count > program_result.seats_total > 0:
            warnings.append(
                DataWarning(
                    kind="overfilled",
                    severity="error",
                    program_id=program_id,
                    university_code=result.university_code,
                    message=(
                        f"«{program_result.title}»: распределено {program_result.allocated_count} "
                        f"при {program_result.seats_total} местах."
                    ),
                )
            )
    return warnings
