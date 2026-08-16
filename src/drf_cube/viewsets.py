"""Viewsets for configurable aggregation endpoints."""

from inspect import isclass
from math import isfinite

from django.core.exceptions import FieldError, ValidationError as DjangoValidationError
from django.db.models import Aggregate, Count, ExpressionWrapper, F, FloatField, Q

try:
    # Django 6.1 wraps aggregate predicates so they participate correctly in
    # expression resolution. Earlier supported Django releases keep the Q
    # object directly on ``Aggregate.filter``.
    from django.db.models.aggregates import AggregateFilter
except ImportError:  # pragma: no cover - exercised by the Django < 6.1 matrix.
    AggregateFilter = None
from django.db.models.functions import Coalesce, NullIf
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.settings import api_settings

from .enums import AggregationScope


class AggregatingViewSet(viewsets.GenericViewSet):
    """
    A list-only viewset that adds aggregation functionality to a DRF endpoint.

    ``fields`` selects public dimensions and therefore the aggregation
    drill-down. ``slice_by_<field>`` applies a configured slice, while
    ``slice_by_<field>__<lookup>`` can slice an omitted public field using a
    supported Django lookup. ``min_`` and ``max_`` parameters filter generated
    aggregate values.

    Subclasses supply ``queryset``, an :class:`AggregatingSerializer` subclass,
    and optionally ordinary DRF filter-backend configuration. Pagination is
    inherited from the consumer's DRF configuration or subclass.
    """

    # Opt in only for trusted/internal endpoints. This lets ordinary URL
    # parameters become Django ORM predicates and lets ``slice_by_`` accept
    # arbitrary ORM paths. The default preserves the narrower public contract.
    permissive_query_parameters = False

    # Names consumed by an application's own filter backend but which should
    # not be treated as ORM predicates in permissive mode.
    permissive_query_parameter_exclusions = frozenset()

    def list(self, request, *args, **kwargs):
        """Group, aggregate, slice, filter, order, and serialize the queryset."""
        self.validate_query_parameters()
        queryset = self.filter_queryset(super().get_queryset())
        queryset = self.apply_permissive_source_filters(queryset)

        group_by_fields = self.get_serializer().get_fields().keys()
        group_by_field_paths = [
            self.serializer_class.field_to_database_path(field)
            for field in group_by_fields
        ]
        queryset = queryset.values(*group_by_field_paths)

        row_aggregates = self.get_aggregates(AggregationScope.ROW)
        queryset = queryset.annotate(**row_aggregates)
        queryset = queryset.filter(
            self.get_filters_by_calculated_fields(row_aggregates)
        )

        slice_aggregates = self.get_aggregates(AggregationScope.SLICE)
        generated_field_names = set(row_aggregates) | set(slice_aggregates)
        if slice_aggregates:
            queryset = queryset.annotate(**slice_aggregates)
            percentage_expressions = self.get_percentage_expressions()
            generated_field_names.update(percentage_expressions)
            queryset = queryset.annotate(**percentage_expressions)
            queryset = queryset.filter(
                self.get_filters_by_calculated_fields(
                    set(slice_aggregates) | set(percentage_expressions)
                )
            )

        explicit_ordering = self.get_explicit_ordering(queryset, generated_field_names)
        if explicit_ordering:
            queryset = queryset.order_by(*explicit_ordering)
        else:
            if slice_aggregates:
                order_by_value_desc = [
                    "-"
                    + self.serializer_class.get_aggregate_field_name(
                        field_name,
                        aggregate,
                        AggregationScope.SLICE,
                        AggregationScope.ROW,
                    )
                    for field_name, aggregate in self.serializer_class.aggregates.items()
                ]
            else:
                order_by_value_desc = [
                    "-"
                    + self.serializer_class.get_aggregate_field_name(
                        field_name, aggregate, AggregationScope.ROW
                    )
                    for field_name, aggregate in self.serializer_class.aggregates.items()
                ]
            queryset = queryset.order_by(*order_by_value_desc)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def get_explicit_ordering(self, queryset, generated_field_names):
        """Normalize explicit ordering for the grouped, annotated queryset."""
        serializer = self.get_serializer()
        ordering_terms = serializer.get_ordering_terms()
        permitted_model_ordering = set()
        for filter_backend in self.filter_backends:
            if issubclass(filter_backend, OrderingFilter):
                permitted_model_ordering.update(
                    filter_backend().remove_invalid_fields(
                        queryset, ordering_terms, self, self.request
                    )
                )

        ordering = []
        for ordering_term in ordering_terms:
            descending = ordering_term.startswith("-")
            field_name = ordering_term.lstrip("-")
            if ordering_term in permitted_model_ordering:
                if field_name in self.serializer_class.Meta.fields:
                    field_name = self.serializer_class.field_to_database_path(
                        field_name
                    )
            elif field_name not in generated_field_names:
                continue

            if field_name in self.serializer_class.Meta.fields:
                field_name = self.serializer_class.field_to_database_path(field_name)
            ordering.append(f"{'-' if descending else ''}{field_name}")
        return ordering

    def get_aggregates(self, scope):
        """Build aggregate annotations for the requested row or slice scope."""
        assert isinstance(scope, AggregationScope)

        if scope == AggregationScope.SLICE:
            slice_filters = self.get_slice_filters()
            if not slice_filters:
                return {}

        aggregates = {}
        for field_name, aggregate in self.serializer_class.aggregates.items():
            if isclass(aggregate):
                aggregate_field_name = self.serializer_class.get_aggregate_field_name(
                    field_name, aggregate, scope
                )
                field_path = self.serializer_class.field_to_database_path(field_name)
                aggregate_args = {}
                if not issubclass(aggregate, Count):
                    aggregate_args = {"default": 0, "output_field": FloatField()}
                if scope == AggregationScope.SLICE:
                    aggregate_args["filter"] = slice_filters
                scoped_aggregate = aggregate(field_path, **aggregate_args)
            else:
                aggregate_field_name = self.serializer_class.get_aggregate_field_name(
                    field_name, aggregate, scope
                )
                scoped_aggregate = aggregate.copy()
                if scope == AggregationScope.SLICE:
                    self.apply_slice_filter(scoped_aggregate, slice_filters)

            aggregates[aggregate_field_name] = scoped_aggregate
        return aggregates

    @staticmethod
    def apply_slice_filter(expression, slice_filters):
        """Apply a selected slice to every aggregate in a custom expression.

        Aggregate instances may already have a ``filter=`` predicate. A slice
        narrows that predicate rather than replacing it. The copied aggregate
        receives the combined filter, keeping the serializer's configured
        expression reusable across requests. Expressions such as
        ``ExpressionWrapper(Sum(...))`` are traversed so their nested
        aggregate receives the same treatment.
        """
        if isinstance(expression, Aggregate):
            configured_filter = expression.filter
            if configured_filter is not None:
                configured_filter = getattr(
                    configured_filter, "condition", configured_filter
                )
                slice_filters = configured_filter & slice_filters

            expression.filter = (
                AggregateFilter(slice_filters)
                if AggregateFilter is not None
                else slice_filters
            )
            return

        source_expressions = expression.get_source_expressions()
        if not source_expressions:
            return
        expression.set_source_expressions(
            [
                source_expression.copy() if source_expression is not None else None
                for source_expression in source_expressions
            ]
        )
        for source_expression in expression.get_source_expressions():
            if source_expression is not None:
                AggregatingViewSet.apply_slice_filter(source_expression, slice_filters)

    def get_slice_filters(self):
        """
        Build the configured and custom slice predicates from query parameters.

        Values within one slice field are ORed. Configured slice fields and
        custom slices are then ANDed together.
        """
        slice_filters = Q()
        for slice_field, slice_expression in self.serializer_class.slice_fields.items():
            slice_filter = Q()
            for item in self.request.query_params.getlist(f"slice_by_{slice_field}"):
                slice_filter |= Q(**{slice_expression: item})
            slice_filters &= slice_filter

        dynamic_slice_fields = (
            set(self.serializer_class.Meta.fields)
            - self.get_serializer().get_fields().keys()
        )
        for field_name in dynamic_slice_fields:
            for lookup_type in sorted(self.serializer_class.dynamic_slice_lookups):
                slice_filter = Q()
                field_path = self.serializer_class.field_to_database_path(field_name)
                slice_expression = f"{field_path}__{lookup_type}"
                for item in self.request.query_params.getlist(
                    f"slice_by_{field_name}__{lookup_type}"
                ):
                    slice_filter |= Q(**{slice_expression: item})
                slice_filters &= slice_filter

        for slice_filter in self.get_permissive_slice_filters().values():
            slice_filters &= slice_filter

        return slice_filters

    def validate_query_parameters(self):
        """Reject unsupported dynamic lookups and non-numeric aggregate bounds."""
        self.validate_dynamic_slice_lookups()
        self.validate_aggregate_bounds()
        self.validate_permissive_query_parameters()

    def validate_dynamic_slice_lookups(self):
        """Ensure every supplied custom slice lookup is explicitly allowed."""
        if self.permissive_query_parameters:
            return

        supported_lookups = self.serializer_class.dynamic_slice_lookups
        allowed = ", ".join(sorted(supported_lookups))
        errors = {}

        for parameter_name in self.request.query_params:
            for field_name in self.serializer_class.Meta.fields:
                prefix = f"slice_by_{field_name}__"
                if not parameter_name.startswith(prefix):
                    continue

                lookup = parameter_name.removeprefix(prefix)
                if lookup not in supported_lookups:
                    errors[parameter_name] = (
                        f"Unsupported dynamic slice lookup '{lookup}' for "
                        f"'{field_name}'. Supported lookups: {allowed}."
                    )
                break

        if errors:
            raise ValidationError(errors)

    @staticmethod
    def prepare_lookup_value(parameter_name, value):
        """Prepare the two URL encodings Django's admin also recognizes."""
        if parameter_name.endswith("__in"):
            return value.split(",")
        if parameter_name.endswith("__isnull"):
            return value.lower() not in ("", "false", "0")
        return value

    def get_permissive_source_filters(self):
        """Return source-query predicates keyed by their URL parameter name."""
        if not self.permissive_query_parameters:
            return {}

        reserved_parameters = self.get_reserved_query_parameter_names()
        filters = {}
        for parameter_name in self.request.query_params:
            if (
                parameter_name in reserved_parameters
                or parameter_name.startswith("slice_by_")
            ):
                continue
            filters[parameter_name] = self.get_query_parameter_filter(
                parameter_name, parameter_name
            )
        return filters

    def get_permissive_slice_filters(self):
        """Return ad-hoc slice predicates keyed by their URL parameter name."""
        if not self.permissive_query_parameters:
            return {}

        dynamic_slice_fields = (
            set(self.serializer_class.Meta.fields)
            - self.get_serializer().get_fields().keys()
        )
        filters = {}
        for parameter_name in self.request.query_params:
            if not parameter_name.startswith("slice_by_"):
                continue

            slice_name = parameter_name.removeprefix("slice_by_")
            if slice_name in self.serializer_class.slice_fields:
                continue

            slice_expression = self.get_permissive_slice_expression(
                slice_name, dynamic_slice_fields
            )
            if slice_expression is not None:
                filters[parameter_name] = self.get_query_parameter_filter(
                    parameter_name, slice_expression
                )
        return filters

    def get_permissive_slice_expression(self, slice_name, dynamic_slice_fields):
        """Map a public dimension when possible, otherwise keep its ORM path."""
        for field_name in self.serializer_class.Meta.fields:
            prefix = f"{field_name}__"
            if not slice_name.startswith(prefix):
                continue

            lookup = slice_name.removeprefix(prefix)
            if (
                field_name in dynamic_slice_fields
                and lookup in self.serializer_class.dynamic_slice_lookups
            ):
                # The regular, documented dynamic-slice path handles this.
                return None
            field_path = self.serializer_class.field_to_database_path(field_name)
            return f"{field_path}__{lookup}"
        return slice_name

    def get_query_parameter_filter(self, parameter_name, orm_expression):
        """OR repeated values for one parameter, as Django admin does."""
        filter_for_parameter = Q()
        for value in self.request.query_params.getlist(parameter_name):
            filter_for_parameter |= Q(
                **{
                    orm_expression: self.prepare_lookup_value(
                        orm_expression, value
                    )
                }
            )
        return filter_for_parameter

    def get_reserved_query_parameter_names(self):
        """Return cube, renderer, and pagination parameters not used as filters."""
        parameter_names = {
            "fields",
            api_settings.ORDERING_PARAM,
            *self.get_aggregate_bound_parameter_names(),
            *self.permissive_query_parameter_exclusions,
        }
        if api_settings.URL_FORMAT_OVERRIDE:
            parameter_names.add(api_settings.URL_FORMAT_OVERRIDE)

        paginator = self.paginator
        if paginator is not None:
            for attribute_name in (
                "page_query_param",
                "page_size_query_param",
                "limit_query_param",
                "offset_query_param",
                "cursor_query_param",
            ):
                parameter_name = getattr(paginator, attribute_name, None)
                if parameter_name:
                    parameter_names.add(parameter_name)
        return parameter_names

    def validate_permissive_query_parameters(self):
        """Turn invalid permissive ORM predicates into parameter-specific 400s."""
        if not self.permissive_query_parameters:
            return

        errors = {}
        queryset = super().get_queryset()
        predicates = {
            **self.get_permissive_source_filters(),
            **self.get_permissive_slice_filters(),
        }
        for parameter_name, predicate in predicates.items():
            try:
                queryset.filter(predicate)
            except (DjangoValidationError, FieldError, TypeError, ValueError) as error:
                errors[parameter_name] = f"Invalid ORM filter: {error}"
        if errors:
            raise ValidationError(errors)

    def apply_permissive_source_filters(self, queryset):
        """Apply the validated source predicates before grouping and aggregation."""
        for predicate in self.get_permissive_source_filters().values():
            queryset = queryset.filter(predicate)
        return queryset

    def validate_aggregate_bounds(self):
        """Ensure aggregate bounds are numeric and refer to live annotations."""
        errors = {}
        available_bound_field_names = set(self.get_aggregate_bound_field_names())
        slice_bound_field_names = set(self.get_slice_bound_field_names())
        has_active_slice = bool(self.get_slice_filters())

        for aggregate_field_name in self.get_declared_aggregate_field_names():
            for prefix in ("min", "max"):
                parameter_name = f"{prefix}_{aggregate_field_name}"
                limit = self.request.query_params.get(parameter_name)
                if limit is None:
                    continue
                if aggregate_field_name not in available_bound_field_names:
                    errors[parameter_name] = (
                        "This bound is unavailable because the configured aggregate "
                        "does not produce this annotation."
                    )
                    continue
                try:
                    numeric_limit = float(limit)
                except (TypeError, ValueError):
                    numeric_limit = None

                if numeric_limit is None or not isfinite(numeric_limit):
                    errors[parameter_name] = "Enter a finite number, for example 12.5."
                elif (
                    aggregate_field_name in slice_bound_field_names
                    and not has_active_slice
                ):
                    errors[parameter_name] = (
                        "This bound requires an active slice selected with a "
                        "slice_by_ parameter."
                    )

        if errors:
            raise ValidationError(errors)

    def get_aggregate_bound_parameter_names(self):
        """Yield supported ``min_`` and ``max_`` aggregate-bound parameters."""
        for aggregate_field_name in self.get_aggregate_bound_field_names():
            for url_param_prefix in ("min", "max"):
                yield f"{url_param_prefix}_{aggregate_field_name}"

    def get_aggregate_bound_field_names(self):
        """Yield every generated aggregate field that accepts a bound."""
        for field_name, aggregate in self.serializer_class.aggregates.items():
            yield self.serializer_class.get_aggregate_field_name(
                field_name, aggregate, AggregationScope.ROW
            )
            yield self.serializer_class.get_aggregate_field_name(
                field_name, aggregate, AggregationScope.SLICE
            )
            yield self.serializer_class.get_aggregate_field_name(
                field_name,
                aggregate,
                AggregationScope.SLICE,
                AggregationScope.ROW,
            )

    def get_declared_aggregate_field_names(self):
        """Yield all conventional names, including unavailable instance slices."""
        for field_name, aggregate in self.serializer_class.aggregates.items():
            yield self.serializer_class.get_aggregate_field_name(
                field_name, aggregate, AggregationScope.ROW
            )
            yield self.serializer_class.get_aggregate_field_name(
                field_name, aggregate, AggregationScope.SLICE
            )
            yield self.serializer_class.get_aggregate_field_name(
                field_name,
                aggregate,
                AggregationScope.SLICE,
                AggregationScope.ROW,
            )

    def get_slice_bound_field_names(self):
        """Yield aggregate fields which exist only after a slice is selected."""
        for field_name, aggregate in self.serializer_class.aggregates.items():
            yield self.serializer_class.get_aggregate_field_name(
                field_name, aggregate, AggregationScope.SLICE
            )
            yield self.serializer_class.get_aggregate_field_name(
                field_name,
                aggregate,
                AggregationScope.SLICE,
                AggregationScope.ROW,
            )

    def get_percentage_expressions(self):
        """Build zero-safe slice-as-a-percentage-of-row annotations."""
        percentage_expressions = {}
        for field_name, aggregate in self.serializer_class.aggregates.items():
            slice_field_name = self.serializer_class.get_aggregate_field_name(
                field_name, aggregate, AggregationScope.SLICE
            )
            denominator_field_name = self.serializer_class.get_aggregate_field_name(
                field_name, aggregate, AggregationScope.ROW
            )

            expression = ExpressionWrapper(
                ExpressionWrapper(
                    F(slice_field_name) * 100.0, output_field=FloatField()
                )
                / NullIf(F(denominator_field_name), 0.0, output_field=FloatField()),
                output_field=FloatField(),
            )
            expression = Coalesce(expression, 0.0, output_field=FloatField())

            percentage_field_name = self.serializer_class.get_aggregate_field_name(
                field_name,
                aggregate,
                AggregationScope.SLICE,
                AggregationScope.ROW,
            )
            percentage_expressions[percentage_field_name] = ExpressionWrapper(
                expression, output_field=FloatField()
            )
        return percentage_expressions

    def get_filters_by_calculated_fields(self, available_field_names):
        """Build bounds only for aggregate annotations already on the queryset."""
        filters_on_aggregates = Q()
        for aggregate_field_name in available_field_names:
            for url_param_prefix, orm_expression in (("min", "gte"), ("max", "lte")):
                url_param_name = f"{url_param_prefix}_{aggregate_field_name}"
                limit = self.request.query_params.get(url_param_name)
                if limit is not None:
                    filters_on_aggregates &= Q(
                        **{f"{aggregate_field_name}__{orm_expression}": float(limit)}
                    )
        return filters_on_aggregates
