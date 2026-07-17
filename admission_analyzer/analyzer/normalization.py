"""Общие утилиты нормализации значений и заголовков.

Схемы колонок конкретных вузов (алиасы заголовков, категории конкурса) живут в
соответствующих адаптерах (`analyzer/universities/*.py`) — здесь только то, что
не зависит от вуза: приведение булевых/числовых значений, чистка заголовков от
регистра/пробелов/неразрывных пробелов, транслитерация в слаг для program_id.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

TRUE_VALUES = {"да", "true", "1", "yes", "y", "+", "истина", "есть"}
FALSE_VALUES = {"нет", "false", "0", "no", "n", "-", "ложь", "отсутствует"}

_PUNCT = re.compile(r"[^\w+ё]+", re.UNICODE)
_SPACES = re.compile(r"\s+")

def header_key(value: Any) -> str:
    """Ключ для сравнения заголовков и категорий.

    Нижний регистр, ё→е, NBSP→пробел, знаки препинания убраны, кроме `+`
    (различает «Балл ВИ» и «Балл ВИ+ИД» / «ВИ 1»/«ВИ1»).
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\xa0", " ").replace("﻿", "")
    text = text.strip().lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    return text


def normalize_bool(value: Any) -> Optional[bool]:
    """«да»/true/1 → True, «нет»/false/0 → False, пусто/«-» → None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value != value:  # NaN
            return None
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    key = header_key(value)
    if not key:
        return None
    if key in TRUE_VALUES:
        return True
    if key in FALSE_VALUES:
        return False
    return None


def normalize_dash_bool(value: Any) -> Optional[bool]:
    """Как `normalize_bool`, но явный «-» — это `None`, а не «нет».

    Нужен для полей ВШЭ вроде «Наличие согласия на зачисление», где «-»
    означает «согласие не подавалось», а не «отказ».
    """
    text = str(value).strip() if value is not None else ""
    if text in {"-", "—", ""}:
        return None
    return normalize_bool(value)


def normalize_int(value: Any) -> Optional[int]:
    """Значение → int. Пустое/нечисловое/«-» → None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:
            return None
        return int(round(value))
    text = str(value).strip().replace("\xa0", "").replace(" ", "")
    if not text or text.lower() in {"nan", "none", "-", "—"}:
        return None
    text = text.replace(",", ".")
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def normalize_code(value: Any) -> Optional[str]:
    """Код абитуриента → строка. Ведущие нули сохраняются.

    pandas может прочитать код как float (`1954871.0`) — хвост `.0` срезаем,
    иначе один и тот же человек не склеится между файлами и вузами.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:
            return None
        if value.is_integer():
            return str(int(value))
        return str(value).strip()
    if isinstance(value, int):
        return str(value)
    text = str(value).strip().replace("\xa0", "").replace(" ", "")
    if not text or text.lower() in {"nan", "none"}:
        return None
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return text


def normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value).strip().replace("\xa0", " ")
    return text or None


def slugify(value: Any) -> str:
    """Название → слаг для program_id: нижний регистр, пробелы → дефисы.

    Кириллица сохраняется как есть (так задано в ТЗ примером
    `itmo:09.03.03:прикладная-информатика:budget`) — слаг остаётся читаемым,
    транслитерация не нужна, program_id и так не используется как URL.
    """
    text = header_key(value).replace(" ", "-")
    slug = re.sub(r"-+", "-", text).strip("-")
    return slug or "programma"


def forward_fill(values: list[Any], normalize) -> list[Any]:
    """Протянуть значение вниз до следующего непустого (после `normalize`)."""
    result: list[Any] = []
    current = None
    for value in values:
        parsed = normalize(value)
        if parsed is not None:
            current = parsed
        result.append(current)
    return result


def first_non_empty(values: list[Any]) -> Optional[str]:
    """Первое непустое значение — для метаданных, продублированных по строкам."""
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return None
