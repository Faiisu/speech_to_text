from urllib.parse import urlsplit

import pytest

from speech_to_text.features.model_deployment import ConfigurationError, normalize_rtsp_source


def test_raw_rtsp_credentials_are_escaped_once_and_hidden():
    config = normalize_rtsp_source(
        "rtsp://camera.local:554/live?token=kept", "operator@desk", "p@ss:/%#"
    )
    assert config.rtsp_url == "rtsp://camera.local:554/live?token=kept"
    assert config.rtsp_username == "operator@desk"
    assert config.rtsp_has_password is True
    private_url = config.audio_source_factory._url
    assert urlsplit(private_url).username == "operator%40desk"
    assert urlsplit(private_url).password == "p%40ss%3A%2F%25%23"
    assert "p@ss" not in repr(config)
    assert "p@ss" not in repr(config.audio_source_factory)


def test_pasted_url_decodes_credentials_once_and_sanitizes_url():
    config = normalize_rtsp_source("rtsps://cam%40user:p%40ss%3A%2F%25%23@camera.local/audio")
    assert config.rtsp_url == "rtsps://camera.local/audio"
    assert config.rtsp_username == "cam@user"
    assert config.rtsp_has_password is True
    assert "cam%40user:p%40ss%3A%2F%25%23@" in config.audio_source_factory._url


@pytest.mark.parametrize("url", [
    "rtsp://user:password@camera/live#fragment",
    "rtsp://user:password@camera/live\n",
    "rtsp://camera/live#fragment",
])
def test_rtsp_normalization_rejects_fragments_and_controls(url):
    with pytest.raises(ConfigurationError):
        normalize_rtsp_source(url)


def test_inline_and_explicit_credentials_are_ambiguous():
    with pytest.raises(ConfigurationError, match="either URL credentials"):
        normalize_rtsp_source("rtsp://user:password@camera/live", "user", "password")
