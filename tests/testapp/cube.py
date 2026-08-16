"""Test-only cube configuration based on the legacy HEA report endpoint."""

from typing import ClassVar

from django.db.models import Avg, F, FloatField, Sum
from rest_framework.filters import BaseFilterBackend, OrderingFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from drf_cube import AggregatingSerializer, AggregatingViewSet

from .models import Fact


class RegionFilterBackend(BaseFilterBackend):
    """A small global filter that must constrain both rows and slices."""

    def filter_queryset(self, request, queryset, view):
        region = request.query_params.get("region")
        return queryset.filter(region=region) if region else queryset


class SmallPagination(PageNumberPagination):
    page_size = None
    page_size_query_param = "page_size"
    max_page_size = 100


class FactCubeSerializer(AggregatingSerializer):
    """Exercises aggregate classes, public names, and relation paths."""

    class Meta:
        model = Fact
        fields = ("region", "category_code", "amount", "quantity")

    aggregates: ClassVar[dict[str, object]] = {
        "amount": Sum,
        "quantity": Avg,
    }
    slice_fields: ClassVar[dict[str, str]] = {
        "category_code": "category__code",
        "region": "region",
    }

    @staticmethod
    def field_to_database_path(field_name):
        return {
            "category_code": "category__code",
            "quantity": "quantity",
        }.get(field_name, field_name)


class FactCubeViewSet(AggregatingViewSet):
    queryset = Fact.objects.all()
    serializer_class = FactCubeSerializer
    filter_backends: ClassVar[list[type[BaseFilterBackend]]] = [
        RegionFilterBackend,
        OrderingFilter,
    ]
    ordering_fields = ("region", "category__code")
    pagination_class = SmallPagination


class PermissiveFactCubeViewSet(FactCubeViewSet):
    permissive_query_parameters = True


class ProtectedFactCubeViewSet(FactCubeViewSet):
    permission_classes: ClassVar[tuple[type[IsAuthenticated], ...]] = (IsAuthenticated,)


class ExpressionFactCubeSerializer(FactCubeSerializer):
    """A constructed aggregate used to verify custom-expression slices."""

    aggregates: ClassVar[dict[str, object]] = {
        "weighted_amount": Sum(F("amount") * F("quantity"), output_field=FloatField()),
    }


class ExpressionFactCubeViewSet(FactCubeViewSet):
    serializer_class = ExpressionFactCubeSerializer
