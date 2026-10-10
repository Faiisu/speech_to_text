"""Versioned public API routes."""

from fastapi import APIRouter

from .routes.microphones import router as microphones_router
from .routes.models import router as models_router
from .routes.profiles import router as profiles_router
from .routes.stress_tests import router as stress_tests_router
from .routes.transcription import router as transcription_router

router = APIRouter()
router.include_router(microphones_router)
router.include_router(models_router)
router.include_router(profiles_router)
router.include_router(stress_tests_router)
router.include_router(transcription_router)
