"""Thai keyword and phrase matching using literal substring searches."""

import unicodedata
from dataclasses import dataclass


class WordMatchingError(ValueError):
    """Raised for invalid match configuration."""


def _normalize(text):
    return unicodedata.normalize("NFC", text).casefold()


@dataclass(frozen=True)
class WordMatchingConfig:
    keywords: tuple[str, ...]
    language: str = "th"

    def __post_init__(self):
        if isinstance(self.keywords, (str, bytes)):
            raise WordMatchingError("keywords must be a non-empty sequence of strings")
        try:
            keywords = tuple(self.keywords)
        except TypeError as exc:
            raise WordMatchingError(
                "keywords must be a non-empty sequence of strings"
            ) from exc
        if not keywords:
            raise WordMatchingError("at least one keyword or phrase is required")
        if any(
            not isinstance(keyword, str) or not keyword.strip() for keyword in keywords
        ):
            raise WordMatchingError("each keyword or phrase must be a non-empty string")
        normalized = [_normalize(keyword) for keyword in keywords]
        if len(set(normalized)) != len(normalized):
            raise WordMatchingError(
                "keywords must be unique after NFC normalization and case folding"
            )
        if self.language != "th":
            raise WordMatchingError(f"Unsupported matching language: {self.language!r}")
        object.__setattr__(self, "keywords", keywords)


@dataclass(frozen=True)
class KeywordMatch:
    keyword: str
    count: int


@dataclass(frozen=True)
class WordMatchResult:
    language: str
    matches: tuple[KeywordMatch, ...]


def _count_occurrences(source, target):
    count = 0
    start = 0
    while True:
        start = source.find(target, start)
        if start < 0:
            return count
        count += 1
        start += 1


def match_keywords(text, config):
    """Count overlapping literal substring occurrences for each target.

    The source and configured targets are normalized to Unicode NFC and
    case-folded before searching. Each target is counted independently.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(config, WordMatchingConfig):
        raise TypeError("config must be a WordMatchingConfig")

    source = _normalize(text)
    matches = tuple(
        KeywordMatch(
            keyword=keyword,
            count=_count_occurrences(source, _normalize(keyword)),
        )
        for keyword in config.keywords
    )
    return WordMatchResult(config.language, matches)
