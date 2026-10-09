"""Thai keyword and phrase matching using exact token sequences."""

from dataclasses import dataclass
import unicodedata


class WordMatchingError(ValueError):
    """Raised for invalid match configuration or unavailable tokenization."""


@dataclass(frozen=True)
class WordMatchingConfig:
    keywords: tuple[str, ...]
    language: str = "th"
    engine: str = "newmm"

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
        normalized = [
            unicodedata.normalize("NFC", keyword).casefold() for keyword in keywords
        ]
        if len(set(normalized)) != len(normalized):
            raise WordMatchingError("keywords must be unique after NFC normalization")
        if self.language != "th":
            raise WordMatchingError(f"Unsupported matching language: {self.language!r}")
        if not isinstance(self.engine, str) or not self.engine.strip():
            raise WordMatchingError("engine must be a non-empty tokenizer name")
        object.__setattr__(self, "keywords", keywords)


@dataclass(frozen=True)
class KeywordMatch:
    keyword: str
    count: int


@dataclass(frozen=True)
class WordMatchResult:
    language: str
    engine: str
    matches: tuple[KeywordMatch, ...]


def _token_segments(tokens):
    segments = []
    current_segment = []
    for token in tokens:
        lexical = []
        for character in token:
            category = unicodedata.category(character)[0]
            if character.isspace():
                if lexical:
                    current_segment.append("".join(lexical))
                    lexical = []
                continue
            if category in {"L", "N", "M"}:
                lexical.append(character)
                continue
            if lexical:
                current_segment.append("".join(lexical))
                lexical = []
            if current_segment:
                segments.append(current_segment)
                current_segment = []
        if lexical:
            current_segment.append("".join(lexical))
    if current_segment:
        segments.append(current_segment)
    return segments


def _tokenize(text, engine):
    try:
        from pythainlp.tokenize import word_tokenize
    except ImportError as exc:
        raise WordMatchingError(
            "Thai matching requires PyThaiNLP; install the 'thai-word-matching' extra"
        ) from exc
    try:
        normalized = unicodedata.normalize("NFC", text)
        tokens = word_tokenize(normalized, engine=engine)
    except (LookupError, NotImplementedError, ValueError) as exc:
        raise WordMatchingError(
            f"Unable to tokenize Thai text with engine {engine!r}: {exc}"
        ) from exc
    return [unicodedata.normalize("NFC", token).casefold() for token in tokens]


def _target_tokens(keyword, engine):
    segments = _token_segments(_tokenize(keyword, engine))
    if len(segments) != 1 or not segments[0]:
        raise WordMatchingError(
            f"Keyword or phrase {keyword!r} must be one token sequence"
        )
    return tuple(segments[0])


def _count_occurrences(source_segments, target):
    target_length = len(target)
    count = 0
    for segment in source_segments:
        for start in range(len(segment) - target_length + 1):
            if tuple(segment[start : start + target_length]) == target:
                count += 1
    return count


def match_keywords(text, config):
    """Count each configured target's exact token-sequence occurrences.

    Source punctuation and other non-word tokens divide matching segments;
    whitespace tokens are ignored, so spaces do not interrupt phrases.
    Occurrences overlap and each configured target is counted independently.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(config, WordMatchingConfig):
        raise TypeError("config must be a WordMatchingConfig")

    source_segments = _token_segments(_tokenize(text, config.engine))

    matches = tuple(
        KeywordMatch(
            keyword=keyword,
            count=_count_occurrences(
                source_segments, _target_tokens(keyword, config.engine)
            ),
        )
        for keyword in config.keywords
    )
    return WordMatchResult(config.language, config.engine, matches)
