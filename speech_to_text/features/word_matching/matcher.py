"""Keyword and phrase matching using literal substring searches."""

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
        if not isinstance(self.language, str) or self.language not in {
            "th",
            "en",
            "auto",
        }:
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


class StreamingWordMatcher:
    """Count matches across ordered text chunks without retaining the transcript."""

    def __init__(self, config):
        if not isinstance(config, WordMatchingConfig):
            raise TypeError("config must be a WordMatchingConfig")
        self.config = config
        self._targets = tuple(_normalize(keyword) for keyword in config.keywords)
        self._tail_length = max(map(len, self._targets)) - 1
        self._tail = ""
        self._counts = [0] * len(self._targets)
        self._has_chunk = False

    def add_chunk(self, text):
        """Add one transcript chunk, joined to the prior chunk by one space."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        chunk = _normalize(text)
        separator = " " if self._has_chunk else ""
        boundary = len(self._tail)
        combined = self._tail + separator + chunk
        for index, target in enumerate(self._targets):
            start = max(0, boundary - len(target) + 1)
            while True:
                start = combined.find(target, start)
                if start < 0:
                    break
                if start + len(target) > boundary:
                    self._counts[index] += 1
                start += 1
        self._tail = (
            combined[-self._tail_length :] if self._tail_length else ""
        )
        self._has_chunk = True
        return WordMatchResult(
            self.config.language,
            tuple(
                KeywordMatch(keyword, count)
                for keyword, count in zip(self.config.keywords, self._counts)
            ),
        )


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
