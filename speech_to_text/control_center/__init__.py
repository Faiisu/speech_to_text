"""Local browser control center shared shell and feature registry."""

from .app import create_app
from .registry import FeatureContribution, FeatureRegistry

__all__ = ["FeatureContribution", "FeatureRegistry", "create_app"]
