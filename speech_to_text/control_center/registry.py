"""Explicit, reviewable feature contributions for the control center."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class FeatureContribution:
    id: str
    name: str
    summary: str
    spec: str
    page_module: str
    page_template: str
    page_stylesheet: str
    router_factory: Callable[[], object]
    process_provider: Callable[[object], object] | None = None
    measurement_provider: Callable[[object], object] | None = None
    implementation_status: str = "implemented"
    verification_status: str = "unverified"


class FeatureRegistry:
    def __init__(self, contributions=()):
        self._features = {}
        for feature in contributions:
            self.register(feature)

    def register(self, contribution: FeatureContribution):
        if not isinstance(contribution, FeatureContribution):
            raise TypeError("feature contribution must be a FeatureContribution")
        if contribution.id in self._features:
            raise ValueError(f"Feature {contribution.id!r} is already registered")
        self._features[contribution.id] = contribution

    def list(self):
        return tuple(self._features.values())

    def get(self, feature_id):
        return self._features.get(feature_id)
