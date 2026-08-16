"""Server-rendered controls and results for an existing cube API endpoint.

The explorer never implements aggregation itself. A report request is routed
through the consumer's existing ``AggregatingViewSet`` list callback, and this
module renders that DRF response with normal Django forms and templates.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from inspect import isclass
from urllib.parse import urlsplit

from django import forms
from django.forms import formset_factory
from django.forms.utils import pretty_name
from django.http import QueryDict
from django.shortcuts import redirect, render
from django.urls import resolve, reverse
from django.utils.text import capfirst
from rest_framework.filters import OrderingFilter
from rest_framework.settings import api_settings

from .enums import AggregationScope


def humanize(value):
    """Return a stable label for a public cube name."""
    return capfirst(pretty_name(value))


def aggregate_label(value, bound=None):
    """Return a scope-first, human-readable aggregate or bound label."""
    for suffix, scope, scope_suffix in (
        ("_slice_percentage_of_row", "Slice", " (% of row)"),
        ("_slice", "Slice", ""),
        ("_row", "Row", ""),
    ):
        if value.endswith(suffix):
            label = (
                f"{scope} {humanize(value.removesuffix(suffix)).lower()}{scope_suffix}"
            )
            break
    else:
        label = humanize(value)

    if bound:
        comparator = {"min": ">=", "max": "<="}[bound]
        return f"{label} {comparator}"
    return label


@dataclass(frozen=True)
class CubeExplorerMetadata:
    """Public, query-free description of an aggregation viewset."""

    dimensions: tuple[str, ...]
    aggregates: tuple[str, ...]
    configured_slices: tuple[str, ...]
    permissive: bool
    dynamic_slice_lookups: tuple[str, ...]
    ordering_fields: tuple[str, ...]
    page_parameter: str
    page_size_parameter: str | None
    reserved_parameters: frozenset[str]

    @classmethod
    def from_viewset(cls, viewset_class):
        """Build metadata without constructing a request or evaluating a queryset."""
        serializer_class = viewset_class.serializer_class
        aggregate_input_paths = {
            serializer_class.field_to_database_path(field_name)
            for field_name, aggregate in serializer_class.aggregates.items()
            if isclass(aggregate)
        }
        dimensions = tuple(
            field_name
            for field_name in serializer_class.Meta.fields
            if serializer_class.field_to_database_path(field_name)
            not in aggregate_input_paths
        )
        aggregates = []
        for field_name, aggregate in serializer_class.aggregates.items():
            for scope, percentage_of in (
                (AggregationScope.ROW, None),
                (AggregationScope.SLICE, None),
                (AggregationScope.SLICE, AggregationScope.ROW),
            ):
                aggregates.append(
                    serializer_class.get_aggregate_field_name(
                        field_name, aggregate, scope, percentage_of
                    )
                )

        ordering_fields = list(dimensions)
        configured_ordering = getattr(viewset_class, "ordering_fields", ())
        if configured_ordering == "__all__":
            configured_ordering = tuple(
                serializer_class.field_to_database_path(field_name)
                for field_name in dimensions
            )
        if any(
            issubclass(filter_backend, OrderingFilter)
            for filter_backend in viewset_class.filter_backends
        ):
            ordering_fields.extend(
                field_name
                for field_name in configured_ordering or ()
                if field_name not in ordering_fields
            )
        ordering_fields.extend(
            name for name in aggregates if name not in ordering_fields
        )

        pagination_class = getattr(viewset_class, "pagination_class", None)
        page_parameter = getattr(pagination_class, "page_query_param", "page")
        page_size_parameter = getattr(pagination_class, "page_size_query_param", None)
        reserved_parameters = {"fields", api_settings.ORDERING_PARAM, page_parameter}
        if api_settings.URL_FORMAT_OVERRIDE:
            reserved_parameters.add(api_settings.URL_FORMAT_OVERRIDE)
        if page_size_parameter:
            reserved_parameters.add(page_size_parameter)
        reserved_parameters.update(
            getattr(viewset_class, "permissive_query_parameter_exclusions", ())
        )
        for name in aggregates:
            reserved_parameters.add(f"min_{name}")
            reserved_parameters.add(f"max_{name}")

        return cls(
            dimensions=dimensions,
            aggregates=tuple(aggregates),
            configured_slices=tuple(serializer_class.slice_fields),
            permissive=bool(
                getattr(viewset_class, "permissive_query_parameters", False)
            ),
            dynamic_slice_lookups=tuple(sorted(serializer_class.dynamic_slice_lookups)),
            ordering_fields=tuple(ordering_fields),
            page_parameter=page_parameter,
            page_size_parameter=page_size_parameter,
            reserved_parameters=frozenset(reserved_parameters),
        )

    @property
    def labels(self):
        return {
            name: aggregate_label(name) if name in self.aggregates else humanize(name)
            for name in (*self.dimensions, *self.aggregates)
        }

    @property
    def slice_bounds(self):
        return tuple(name for name in self.aggregates if "_slice" in name)


class _PredicateForm(forms.Form):
    lookup = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. category__code__icontains"}),
    )
    value = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. food"}),
    )
    reserved_parameters = frozenset()
    source = False

    def clean(self):
        cleaned = super().clean()
        lookup = (cleaned.get("lookup") or "").strip()
        value = (cleaned.get("value") or "").strip()
        if not lookup and not value:
            return cleaned
        if not lookup or not value:
            raise forms.ValidationError("Enter both a lookup and a value.")
        if lookup.startswith("slice_by_"):
            self.add_error("lookup", "Lookups must not start with 'slice_by_'.")
        if self.source and lookup in self.reserved_parameters:
            self.add_error(
                "lookup", "This cube control cannot be used as a source lookup."
            )
        return cleaned


def _predicate_form(metadata, *, source):
    return type(
        "SourcePredicateForm" if source else "SlicePredicateForm",
        (_PredicateForm,),
        {"reserved_parameters": metadata.reserved_parameters, "source": source},
    )


def _has_active_slice(metadata, query):
    if any(
        query.get(f"slice_by_{name}", "").strip() for name in metadata.configured_slices
    ):
        return True
    if any(name.startswith("slice_by_") for name in query):
        return True
    try:
        form_count = int(query.get("slice-TOTAL_FORMS", 0) or 0)
    except (TypeError, ValueError):
        return False
    for index in range(form_count):
        if (
            query.get(f"slice-{index}-lookup", "").strip()
            and query.get(f"slice-{index}-value", "").strip()
        ):
            return True
    return False


def _form_class(metadata, *, has_active_slice):
    fields = {
        "dimensions": forms.MultipleChoiceField(
            choices=[(name, humanize(name)) for name in metadata.dimensions],
            required=False,
            widget=forms.CheckboxSelectMultiple,
            help_text="Choose the dimensions used to group the result.",
        ),
    }
    for slice_name in metadata.configured_slices:
        fields[f"slice_by_{slice_name}"] = forms.CharField(
            required=False,
            label=humanize(slice_name),
            help_text="Comma-separated comparison values.",
        )
    for aggregate in metadata.aggregates:
        if aggregate in metadata.slice_bounds and not has_active_slice:
            continue
        for prefix in ("min", "max"):
            fields[f"{prefix}_{aggregate}"] = forms.DecimalField(
                required=False,
                label=aggregate_label(aggregate, prefix),
                widget=forms.NumberInput(attrs={"step": "any"}),
            )
    fields[api_settings.ORDERING_PARAM] = forms.ChoiceField(
        required=False,
        label="Order results",
        choices=[("", "API default")]
        + [
            (direction + name, f"{humanize(name)} ({label})")
            for name in metadata.ordering_fields
            for direction, label in (("-", "descending"), ("", "ascending"))
        ],
    )
    fields[metadata.page_parameter] = forms.IntegerField(
        required=False,
        min_value=1,
        label="Page",
    )
    if metadata.page_size_parameter:
        fields[metadata.page_size_parameter] = forms.IntegerField(
            required=False,
            min_value=1,
            label="Results per page",
        )
    return type("CubeExplorerForm", (forms.Form,), fields)


def _initial_from_api_query(metadata, query):
    initial = {
        "dimensions": query.get("fields", ",".join(metadata.dimensions)).split(","),
        api_settings.ORDERING_PARAM: query.get(api_settings.ORDERING_PARAM, ""),
        metadata.page_parameter: query.get(metadata.page_parameter, ""),
    }
    if metadata.page_size_parameter:
        initial[metadata.page_size_parameter] = query.get(
            metadata.page_size_parameter, ""
        )
    for slice_name in metadata.configured_slices:
        initial[f"slice_by_{slice_name}"] = ",".join(
            query.getlist(f"slice_by_{slice_name}")
        )
    for aggregate in metadata.aggregates:
        for prefix in ("min", "max"):
            initial[f"{prefix}_{aggregate}"] = query.get(f"{prefix}_{aggregate}", "")
    return initial


def cube_explorer_form(metadata, query=None, *, data=None):
    """Build a bound editor form or hydrate one from an API query string."""
    query = query or QueryDict("")
    form_class = _form_class(
        metadata, has_active_slice=_has_active_slice(metadata, data or query)
    )
    if data is not None:
        return form_class(data)
    return form_class(initial=_initial_from_api_query(metadata, query))


def _predicate_initial(metadata, query):
    source_initial, slice_initial = [], []
    configured_slice_parameters = {
        f"slice_by_{name}" for name in metadata.configured_slices
    }
    for parameter in query:
        values = query.getlist(parameter)
        if parameter in configured_slice_parameters:
            continue
        if parameter.startswith("slice_by_"):
            slice_initial.extend(
                {"lookup": parameter.removeprefix("slice_by_"), "value": value}
                for value in values
            )
        elif parameter not in metadata.reserved_parameters:
            source_initial.extend(
                {"lookup": parameter, "value": value} for value in values
            )
    return source_initial, slice_initial


def _predicate_formset(metadata, *, source, data=None, initial=None):
    formset = formset_factory(
        _predicate_form(metadata, source=source),
        extra=1,
        max_num=20,
        validate_max=True,
        can_delete=True,
    )
    kwargs = {"prefix": "source" if source else "slice"}
    if data is not None:
        kwargs["data"] = data
    else:
        kwargs["initial"] = initial or []
    return formset(**kwargs)


def predicate_formsets(metadata, query=None, *, data=None):
    """Build bound predicate formsets or hydrate them from an API query."""
    if not metadata.permissive:
        return None, None
    if data is not None:
        return (
            _predicate_formset(metadata, source=True, data=data),
            _predicate_formset(metadata, source=False, data=data),
        )
    source_initial, slice_initial = _predicate_initial(metadata, query or QueryDict(""))
    return (
        _predicate_formset(metadata, source=True, initial=source_initial),
        _predicate_formset(metadata, source=False, initial=slice_initial),
    )


def _formset_initial(formset):
    return [
        {"lookup": data["lookup"], "value": data["value"]}
        for data in formset.cleaned_data
        if data.get("lookup") and data.get("value") and not data.get("DELETE")
    ]


def _normalized_editor_forms(metadata, form, source_formset, slice_formset):
    """Return unbound controls after a valid server-side editor action."""
    normalized_form = _form_class(
        metadata,
        has_active_slice=bool(
            any(
                form.cleaned_data.get(f"slice_by_{name}")
                for name in metadata.configured_slices
            )
            or (slice_formset and _formset_initial(slice_formset))
        ),
    )(initial=form.cleaned_data)
    if not metadata.permissive:
        return normalized_form, None, None
    return (
        normalized_form,
        _predicate_formset(
            metadata, source=True, initial=_formset_initial(source_formset)
        ),
        _predicate_formset(
            metadata, source=False, initial=_formset_initial(slice_formset)
        ),
    )


def api_query_from_controls(metadata, form, source_formset, slice_formset):
    """Convert valid server-rendered controls into the existing API query."""
    query = QueryDict("", mutable=True)
    dimensions = form.cleaned_data["dimensions"] or list(metadata.dimensions)
    query["fields"] = ",".join(dimensions)
    for slice_name in metadata.configured_slices:
        value = form.cleaned_data[f"slice_by_{slice_name}"]
        for part in value.split(","):
            if part.strip():
                query.appendlist(f"slice_by_{slice_name}", part.strip())
    for aggregate in metadata.aggregates:
        for prefix in ("min", "max"):
            name = f"{prefix}_{aggregate}"
            if name in form.cleaned_data and form.cleaned_data[name] is not None:
                query[name] = str(form.cleaned_data[name])
    for name in (
        api_settings.ORDERING_PARAM,
        metadata.page_parameter,
        metadata.page_size_parameter,
    ):
        if name and form.cleaned_data.get(name) not in (None, ""):
            query[name] = str(form.cleaned_data[name])
    for formset, prefix in ((source_formset, ""), (slice_formset, "slice_by_")):
        if formset is None:
            continue
        for data in formset.cleaned_data:
            if data.get("DELETE") or not data.get("lookup") or not data.get("value"):
                continue
            query.appendlist(f"{prefix}{data['lookup']}", data["value"])
    return query


@dataclass(frozen=True)
class CubeExplorerTable:
    columns: tuple[tuple[str, str], ...]
    rows: tuple[tuple[object, ...], ...]
    previous_url: str | None
    next_url: str | None


def _explorer_page_url(request, query, page_parameter, target):
    if not target:
        return None
    target_query = QueryDict(urlsplit(target).query)
    page = target_query.get(page_parameter)
    if not page:
        return None
    page_query = query.copy()
    page_query[page_parameter] = page
    return f"{request.path}?{page_query.urlencode()}"


def table_from_api_payload(request, metadata, query, payload):
    """Prepare API response dictionaries for an ordinary Django table loop."""
    rows = payload if isinstance(payload, list) else payload.get("results", [])
    columns = tuple(rows[0]) if rows else ()
    return CubeExplorerTable(
        columns=tuple(
            (name, metadata.labels.get(name, humanize(name))) for name in columns
        ),
        rows=tuple(tuple(row.get(name, "") for name in columns) for row in rows),
        previous_url=_explorer_page_url(
            request,
            query,
            metadata.page_parameter,
            None if isinstance(payload, list) else payload.get("previous"),
        ),
        next_url=_explorer_page_url(
            request,
            query,
            metadata.page_parameter,
            None if isinstance(payload, list) else payload.get("next"),
        ),
    )


def _api_error_items(payload):
    if not isinstance(payload, dict):
        return ()
    return tuple(
        (name, "; ".join(value) if isinstance(value, list) else str(value))
        for name, value in payload.items()
    )


def invoke_cube_api(request, api_url, query):
    """Call the configured routed API callback with the current identity and query."""
    match = resolve(api_url)
    api_request = copy(request)
    api_request.path = api_url
    api_request.path_info = api_url
    api_request.GET = query
    api_request.META = request.META.copy()
    api_request.META["HTTP_ACCEPT"] = "application/json"
    api_request.resolver_match = match
    return match.func(api_request, *match.args, **match.kwargs)


def _is_editor_submission(query):
    return (
        "editor_action" in query
        or "dimensions" in query
        or "source-TOTAL_FORMS" in query
    )


def cube_explorer_page(
    *, api_url_name, cube_viewset_class, title="Cube explorer", description=""
):
    """Return a normal Django report page bound to one existing cube API URL."""
    metadata = CubeExplorerMetadata.from_viewset(cube_viewset_class)

    def page(request):
        api_url = reverse(api_url_name)
        if _is_editor_submission(request.GET):
            form = cube_explorer_form(metadata, data=request.GET)
            source_formset, slice_formset = predicate_formsets(
                metadata, data=request.GET
            )
            valid = form.is_valid() and (
                not metadata.permissive
                or (source_formset.is_valid() and slice_formset.is_valid())
            )
            if request.GET.get("editor_action") == "run" and valid:
                query = api_query_from_controls(
                    metadata, form, source_formset, slice_formset
                )
                return redirect(f"{request.path}?{query.urlencode()}")
            if valid:
                form, source_formset, slice_formset = _normalized_editor_forms(
                    metadata, form, source_formset, slice_formset
                )
            return render(
                request,
                "drf_cube/cube_explorer.html",
                {
                    "title": title,
                    "description": description,
                    "api_url": api_url,
                    "form": form,
                    "source_formset": source_formset,
                    "slice_formset": slice_formset,
                },
            )

        form = cube_explorer_form(metadata, request.GET)
        source_formset, slice_formset = predicate_formsets(metadata, request.GET)
        context = {
            "title": title,
            "description": description,
            "api_url": f"{api_url}?{request.GET.urlencode()}"
            if request.GET
            else api_url,
            "form": form,
            "source_formset": source_formset,
            "slice_formset": slice_formset,
        }
        if request.GET:
            response = invoke_cube_api(request, api_url, request.GET)
            payload = getattr(response, "data", {})
            context["report_requested"] = True
            if response.status_code < 400:
                context["table"] = table_from_api_payload(
                    request, metadata, request.GET, payload
                )
            else:
                context["report_error"] = (
                    "You are not authorized to view these results."
                    if response.status_code in (401, 403)
                    else "The API could not process this report."
                )
                context["report_error_items"] = _api_error_items(payload)
        return render(request, "drf_cube/cube_explorer.html", context)

    return page
