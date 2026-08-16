"""Shared aggregation scope values."""

from enum import StrEnum


class AggregationScope(StrEnum):
    """Scopes used to name generated aggregate fields."""

    ROW = "row"
    SLICE = "slice"
