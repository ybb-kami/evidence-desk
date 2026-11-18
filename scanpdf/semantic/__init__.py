"""Optional offline multilingual dense evidence retrieval."""

from .assets import EXPORT, MODEL, REVISION
from .index import DenseIndex, DenseUnavailable

__all__ = ("DenseIndex", "DenseUnavailable", "MODEL", "EXPORT", "REVISION")
