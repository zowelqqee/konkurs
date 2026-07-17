"""Абстрактный интерфейс адаптера вуза.

Новый вуз добавляется реализацией четырёх методов ниже — остальной код
(discovery, allocation, cross_university, reports) с конкретными вузами не
взаимодействует напрямую, только через этот интерфейс и `ProgramMetadata` /
`Application`. См. README, раздел «Добавление нового адаптера».
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Application, DataWarning, DetectionResult, ProgramMetadata
    from ..readers import RawTable


class UniversityAdapter(ABC):
    """Один вуз = одна реализация. Поля ниже заполняются в подклассе."""

    university_code: str
    university_name: str

    @abstractmethod
    def detect(self, raw_table: "RawTable", file_path: Path) -> "DetectionResult":
        """Оценить, похож ли файл на формат этого вуза, и с какой уверенностью."""

    @abstractmethod
    def parse_program_metadata(self, raw_table: "RawTable", file_path: Path) -> "ProgramMetadata":
        """Извлечь метаданные программы (название, места, филиал и т.д.)."""

    @abstractmethod
    def normalize_applications(
        self, raw_table: "RawTable", metadata: "ProgramMetadata"
    ) -> list["Application"]:
        """Превратить строки таблицы в список нормализованных заявлений."""

    @abstractmethod
    def validate(
        self, applications: list["Application"], metadata: "ProgramMetadata"
    ) -> list["DataWarning"]:
        """Проверки данных, специфичные для формата этого вуза."""
