from .base import UniversityAdapter
from .hse import HSEAdapter
from .itmo import ITMOAdapter
from .registry import ADAPTERS, adapters_by_code, detect_all, resolve_adapter

__all__ = [
    "ADAPTERS",
    "HSEAdapter",
    "ITMOAdapter",
    "UniversityAdapter",
    "adapters_by_code",
    "detect_all",
    "resolve_adapter",
]
