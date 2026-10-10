import unicodedata

import pytest

from speech_to_text.features.word_matching import (
    KeywordMatch,
    WordMatchingConfig,
    WordMatchingError,
    WordMatchResult,
    match_keywords,
)


def test_matches_a_keyword_inside_a_larger_string():
    config = WordMatchingConfig(keywords=("ประเทศไทย",))

    result = match_keywords("ฉันชอบประเทศไทยมาก", config)

    assert result == WordMatchResult(
        language="th",
        matches=(KeywordMatch(keyword="ประเทศไทย", count=1),),
    )


def test_counts_overlapping_occurrences_independently_for_each_keyword():
    config = WordMatchingConfig(keywords=("aa", "aaa"))

    result = match_keywords("aaaa", config)

    assert result.matches == (
        KeywordMatch(keyword="aa", count=3),
        KeywordMatch(keyword="aaa", count=2),
    )


def test_normalizes_source_and_keywords_to_nfc_and_casefolds_them():
    composed_keyword = "Café"
    decomposed_source = unicodedata.normalize("NFD", "CAFÉ")
    config = WordMatchingConfig(keywords=(composed_keyword, "STRASSE"))

    result = match_keywords(f"{decomposed_source} Straße", config)

    assert result.matches == (
        KeywordMatch(keyword="Café", count=1),
        KeywordMatch(keyword="STRASSE", count=1),
    )


def test_treats_punctuation_and_whitespace_as_literal_characters():
    config = WordMatchingConfig(keywords=("hello, world", "hello world", "hello!"))

    result = match_keywords("hello, world hello! hello  world", config)

    assert result.matches == (
        KeywordMatch(keyword="hello, world", count=1),
        KeywordMatch(keyword="hello world", count=0),
        KeywordMatch(keyword="hello!", count=1),
    )


def test_includes_configured_keywords_with_zero_occurrences():
    config = WordMatchingConfig(keywords=("สวัสดี", "ลาก่อน"))

    result = match_keywords("สวัสดี", config)

    assert result.matches == (
        KeywordMatch(keyword="สวัสดี", count=1),
        KeywordMatch(keyword="ลาก่อน", count=0),
    )


@pytest.mark.parametrize(
    "keywords",
    [(), "สวัสดี", ("",), ("   ",), ("สวัสดี", None)],
)
def test_rejects_empty_or_invalid_keyword_sequences(keywords):
    with pytest.raises(WordMatchingError):
        WordMatchingConfig(keywords=keywords)


def test_rejects_keywords_that_duplicate_after_normalization():
    with pytest.raises(WordMatchingError):
        WordMatchingConfig(keywords=("Café", unicodedata.normalize("NFD", "CAFÉ")))


def test_rejects_unsupported_matching_languages():
    with pytest.raises(WordMatchingError):
        WordMatchingConfig(keywords=("hello",), language="en")


@pytest.mark.parametrize("text", [None, 42, "สวัสดี".encode()])
def test_rejects_non_string_source_text(text):
    config = WordMatchingConfig(keywords=("สวัสดี",))

    with pytest.raises(TypeError):
        match_keywords(text, config)


def test_rejects_non_config_argument():
    with pytest.raises(TypeError):
        match_keywords("สวัสดี", ("สวัสดี",))
