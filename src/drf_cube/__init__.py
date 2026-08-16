"""Public API for configurable Django REST Framework aggregation endpoints."""

from .enums import AggregationScope
from .serializers import AggregatingSerializer
from .viewsets import AggregatingViewSet

__all__ = (
    "AggregatingSerializer",
    "AggregatingViewSet",
    "AggregationScope",
)

__version__ = "0.2.0"
