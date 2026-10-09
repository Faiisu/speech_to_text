"""Load the new feature only when a test runs; missing code is a failing prerequisite."""

import importlib

import pytest


@pytest.fixture
def api():
    try:
        return importlib.import_module("speech_to_text.features.model_deployment")
    except ModuleNotFoundError as error:
        if error.name in {
            "speech_to_text",
            "speech_to_text.features",
            "speech_to_text.features.model_deployment",
        }:
            pytest.fail("Feature 01 is not implemented: speech_to_text.features.model_deployment", pytrace=False)
        raise
