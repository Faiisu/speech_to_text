"""Compose the HTTP adapters for Feature 01."""

from fastapi import APIRouter

from .models import create_model_router
from .process_groups import create_process_group_router
from .sessions import create_session_router
from .state import FeatureState


def create_router(
    *,
    state=None,
    runtime_factory=None,
    audio_source_factory=None,
    process_audio_source_factory=None,
):
    """Build the Feature 01 routes around one shared lifecycle state."""
    state = state or FeatureState(
        runtime_factory=runtime_factory,
        audio_source_factory=audio_source_factory,
        process_audio_source_factory=process_audio_source_factory,
    )
    router = APIRouter()
    router.state = state
    router.include_router(create_model_router(state))
    router.include_router(create_session_router(state))
    router.include_router(create_process_group_router(state))
    return router
