# Configuration reference

`drf-cube` is configured in the consuming Django project. It does not require
an installed Django app or add any models, migrations, settings, or views.

## Serializer

Subclass `AggregatingSerializer` with:

- `Meta.model`: the queryset model.
- `Meta.fields`: the public dimensions that may be selected with `fields`.
- `aggregates`: public quantity names mapped to aggregate classes or a
  preconstructed aggregate-containing expression.
- `slice_fields`: named slices mapped to complete Django ORM lookup expressions.

Override `field_to_database_path(field_name)` for public names that differ
from model paths. It may map to related paths such as `category__code`; a
consumer may also use active localization context to choose a path while
retaining a stable public response name.

```python
from django.db.models import Sum


class SaleCubeSerializer(AggregatingSerializer):
    class Meta:
        model = Sale
        fields = ("region", "product_code", "amount")

    aggregates = {"amount": Sum}
    slice_fields = {
        "product_prefix": "items__product__code__istartswith",
    }

    @staticmethod
    def field_to_database_path(field_name):
        return {"product_code": "items__product__code"}.get(field_name, field_name)
```

`?fields=region` controls both the dimensions in the response and the
`GROUP BY` drill-down. Unknown requested public fields are ignored. Aggregate
class fields are generated as `<quantity>_<aggregate>_row`, plus `_slice` and
`_slice_percentage_of_row` when an active slice exists.

## Slices, filters, and bounds

Configured slices use `slice_by_<slice_name>`. Repeat a parameter to OR its
values; supply different slice names to AND their predicates. A configured
slice expression contains its Django lookup, so it can use behavior such as
case-insensitive exact matching or prefix matching. For example:

```text
?fields=region&slice_by_product_prefix=R0&slice_by_product_prefix=B01
```

Dynamic slices use `slice_by_<omitted_public_field>__<lookup>=value` and are
limited by `dynamic_slice_lookups`. Restrict or extend that serializer
allowlist deliberately; unsupported lookups return `400`.

Normal DRF filter backends run before cube grouping. Their filters constrain
the row denominator and every active slice numerator. Configure pagination and
filter backends on the consuming viewset as you would for any other DRF
`GenericViewSet`.

### Deliberately permissive URL parameters

For a trusted internal endpoint, set
`permissive_query_parameters = True` on the viewset. Ordinary URL parameters
then become Django ORM filters for the source queryset, while
`slice_by_...` accepts an arbitrary Django ORM path for the slice numerator:

```python
class InternalSaleCubeViewSet(AggregatingViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleCubeSerializer
    permissive_query_parameters = True
```

```text
?customer__code__icontains=xyz
&slice_by_customer__code__icontains=preferred
```

The source filter constrains every row and slice aggregate. The `slice_by_`
filter affects only `_slice` and `_slice_percentage_of_row` results. Repeated
values for one parameter are ORed; different parameters are ANDed. `__in`
accepts a comma-delimited value and `__isnull` recognizes `false` and `0`.

This mode is disabled by default because it exposes the model's ORM lookup
surface. Cube parameters (`fields`, bounds, ordering), renderer parameters,
and pagination parameters remain controls rather than filters. If an
application's own filter backend consumes additional parameter names, list
them in `permissive_query_parameter_exclusions`; invalid ORM filters return a
parameter-specific `400`.

Every available generated aggregate field accepts inclusive `min_<field>` and
`max_<field>` bounds. Row bounds work with or without a slice. Slice and
slice-percentage bounds need an active `slice_by_...` parameter; otherwise the
response is `400`. Non-numeric and non-finite bounds also return `400`.

Preconstructed aggregate-containing expressions create row, slice, and
slice-percentage annotations just like aggregate classes. Their configured
`filter=` predicate, when present, is combined with the selected slice. Bounds
and ordering work on all of those generated annotations.

## Ordering

With DRF's `OrderingFilter` enabled, `ordering` supports ordinary fields,
descending fields, and comma-separated multi-field orderings. It is applied
after generated aggregate annotations, and overrides the default descending
aggregate order.

## Optional Cube Explorer page

The optional explorer renders native Django controls from an existing
`AggregatingViewSet` configuration. A normal Django GET form creates the
canonical API query; the Explorer then calls that routed ViewSet action and
renders its response in the page. It is a UI for the existing contract, not
another aggregation implementation or metadata endpoint.

Install `"drf_cube"`, configure a Django template backend with `APP_DIRS=True`,
and bind the page to the existing router URL name. The explorer is a standalone
page: it does not require Django admin, Bootstrap, admin URLs, model
registrations, staff access, or the admin app's session/message setup:

```python
from django.urls import path
from drf_cube.explorer import cube_explorer_page

urlpatterns = [
    path(
        "reports/sales/",
        cube_explorer_page(
            api_url_name="sales-list",
            cube_viewset_class=SaleCubeViewSet,
            title="Sales cube",
        ),
    ),
]
```

This does not require an admin URL, model, migration, admin registration, or
staff access. The API remains the security boundary: its permission classes,
queryset/tenant restrictions, ordinary filters, and throttles apply when the
Explorer invokes the routed list action. Protect the ordinary page route
separately only when the public configuration metadata itself is sensitive.

The report URL uses the API's parameters directly. Selecting source filters
such as `customer__code__icontains=xyz` narrows the records used for row,
slice, and percentage values. Selecting a comparison slice such as
`slice_by_customer__code__icontains=preferred` affects only `_slice` and
`_slice_percentage_of_row`; row totals remain unchanged. Repeated keys remain
repeated so the API's OR semantics are preserved. Formset changes and report
navigation are ordinary server-rendered GET requests; JavaScript is not
required.

The explorer's dimension checkboxes omit any public field used as the input to
an aggregate class. This avoids offering a raw measure such as `amount` beside
its generated `amount_sum_row` total. It selects its remaining dimensions by
default and returns to that selection when all are cleared. The underlying API
still accepts every field declared in `Meta.fields`, including those inputs,
for detailed URLs.
