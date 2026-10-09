"""Callable Thai keyword and phrase matching feature."""

from .matcher import (
    KeywordMatch,
    WordMatchResult,
    WordMatchingConfig,
    WordMatchingError,
    match_keywords,
)

__all__ = [
    "KeywordMatch",
    "WordMatchResult",
    "WordMatchingConfig",
    "WordMatchingError",
    "match_keywords",
]
