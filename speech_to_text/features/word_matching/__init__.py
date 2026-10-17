"""Callable keyword and phrase matching feature."""

from .matcher import (
    KeywordMatch,
    StreamingWordMatcher,
    WordMatchResult,
    WordMatchingConfig,
    WordMatchingError,
    match_keywords,
)

__all__ = [
    "KeywordMatch",
    "StreamingWordMatcher",
    "WordMatchResult",
    "WordMatchingConfig",
    "WordMatchingError",
    "match_keywords",
]
