"""Универсальное чтение .tsv/.csv/.xlsx в промежуточный `RawTable`.

Этот модуль ничего не знает про конкретный вуз — он только превращает файл в
DataFrame максимально бережно (кодировка, разделитель, мусорные строки/столбцы),
чтобы адаптеры вузов могли распознавать формат по колонкам.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

SUPPORTED_SUFFIXES = {".tsv", ".csv", ".xlsx", ".xlsm"}
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")


class ReaderError(Exception):
    """Понятная пользователю ошибка чтения файла."""


@dataclass
class RawTable:
    """Файл, прочитанный в DataFrame, но ещё не отнесённый к вузу."""

    dataframe: pd.DataFrame
    source_path: Path
    encoding: Optional[str]
    delimiter: Optional[str]

    @property
    def columns(self) -> list[str]:
        return [str(c) for c in self.dataframe.columns]

    @property
    def is_empty(self) -> bool:
        return self.dataframe.empty


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Декодировать с перебором UTF-8 BOM → UTF-8 → cp1251."""
    last: Optional[Exception] = None
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            last = exc
    raise ReaderError(
        f"Не удалось декодировать файл ни одной из кодировок {', '.join(ENCODINGS)}: {last}"
    )


def sniff_delimiter(text: str, filename: str = "") -> str:
    """Определить разделитель. Подсказка по расширению, затем csv.Sniffer."""
    sample = "\n".join(text.splitlines()[:20])
    if not sample.strip():
        raise ReaderError("Файл пуст.")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    header = sample.splitlines()[0]
    counts = {d: header.count(d) for d in ("\t", ";", ",", "|")}
    best = max(counts, key=counts.get)
    if counts[best] > 0:
        return best
    return "\t" if Path(filename).suffix.lower() == ".tsv" else ","


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Убрать полностью пустые строки и столбцы, почистить NBSP в заголовках."""
    df = df.rename(columns=lambda c: str(c).replace("\xa0", " ").strip() if c is not None else c)
    df = df.dropna(axis=1, how="all")
    df = df.loc[:, [c for c in df.columns if str(c).strip() and not str(c).startswith("Unnamed")]]
    df = df.dropna(axis=0, how="all")
    return df


def read_table_from_bytes(raw: bytes, filename: str) -> RawTable:
    """Разобрать один файл (bytes) в RawTable."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ReaderError(f"Неподдерживаемое расширение файла: «{filename}».")

    if suffix in {".xlsx", ".xlsm"}:
        try:
            df = pd.read_excel(io.BytesIO(raw), dtype=object, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001 - показываем причину пользователю
            raise ReaderError(f"Не удалось прочитать Excel-файл «{filename}»: {exc}") from exc
        return RawTable(dataframe=_clean_frame(df), source_path=Path(filename), encoding=None, delimiter=None)

    if not raw.strip():
        return RawTable(dataframe=pd.DataFrame(), source_path=Path(filename), encoding=None, delimiter=None)

    text, encoding = decode_bytes(raw)
    if not text.strip():
        return RawTable(dataframe=pd.DataFrame(), source_path=Path(filename), encoding=encoding, delimiter=None)

    delimiter = sniff_delimiter(text, filename)
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=delimiter,
            dtype=object,
            keep_default_na=False,
            na_values=[""],
        )
    except Exception as exc:  # noqa: BLE001
        raise ReaderError(f"Не удалось разобрать «{filename}»: {exc}") from exc

    return RawTable(
        dataframe=_clean_frame(df), source_path=Path(filename), encoding=encoding, delimiter=delimiter
    )


def read_table(path: str | Path) -> RawTable:
    path = Path(path)
    return read_table_from_bytes(path.read_bytes(), path.name)


def find_data_files(directory: str | Path) -> list[Path]:
    """Найти все .tsv/.csv/.xlsx в папке (без служебных ~$ файлов Excel)."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ReaderError(f"Папка не найдена: {directory}")
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not p.name.startswith("~$")
    )
