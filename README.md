# drf-cube

`drf-cube` turns a Django schema into a small, configurable
data cube REST endpoint, allowing users to explore fact data by creating ad-hoc aggregated queries. 
Users choose the dimensions to drill in to ('group by' in SQL), filters to apply, facts of interest, 
and row breakdowns to report, dynamically in the URL, so users can explore data flexibly and interactively. 
The endpoint applies filters and returns totals and row composition over those dimensions. 
Aggregation is done by the database using the Django ORM, so is reasonably efficient. 

It is a library, not a Django application. It adds no models, migrations,
views, admin, or settings; configure a serializer and viewset around models you
already own.

## What you can build with it

Configure
the public dimensions and measures once; clients then choose the level of
aggregation in the URL. For example, the same sales endpoint can return one
total for all sales, totals by region, or totals by region and product
category, and show the proportion of each row by product. The cube groups 
across model relationships out of the box, using your Django schema.

- **Choose the grain.** `fields` says which public dimensions to include in
  the response. `drf-cube` groups by those dimensions and aggregates the
  remaining records, so one endpoint supports both a headline total and a
  detailed drill-down.
- **Use Django measures.** A measure may use any Django aggregate class, such
  as `Sum`, `Avg`, `Count`, `Min`, or `Max`, or a preconstructed
  aggregate-containing expression for calculations such as `Sum(F("price") *
  F("quantity"))`.
- **Filter before and after aggregation.** Normal DRF filter backends narrow
  the source records before grouping. Inclusive `min_...` and `max_...`
  parameters then retain only groups whose calculated totals, slice totals, or
  percentages meet a threshold. Trusted internal endpoints can additionally
  opt into ad-hoc Django ORM filters from URL parameters.
- **Show composition with slices.** A `slice_by_...` parameter adds a second
  aggregate for a selected sub-dimension and its percentage of each row. For
  example, grouping sales by `region` and selecting
  `slice_by_category_code=clothes` returns every region's total sales, its
  clothes sales, and the percentage of that region's sales that were clothes.

In short, configure what is safe and meaningful for your product; let a
dashboard or trusted analyst vary the grouping, filters, composition question,
thresholds, ordering, and pagination without needing a new report endpoint.

## Steps

1. Subclass `AggregatingSerializer` with `Meta.model` and the complete public
   dimension list in `Meta.fields`.
2. Add Django aggregate classes or preconstructed aggregate-containing
   expressions to `aggregates`.
3. Override `field_to_database_path()` when a public name differs from a model
   field or must traverse a relation.
4. Add `slice_fields` for named, deliberately supported slice predicates.
5. Configure normal DRF filter backends, ordering, and pagination on the
   viewset as required by your project.
6. Use `fields`, `slice_by_...`, and `min_`/`max_` query parameters from the
   client to choose the report shape.

For the complete configuration reference, including the API contract for
validation errors and aggregate naming, see
[docs/configuration.md](docs/configuration.md).


## Follow a sales manager's investigation

Imagine Maya, a regional sales manager, has a transaction table but no fixed
report that answers her questions. She wants to start broad—where are sales
coming from?—then understand the mix behind each region, focus on material
opportunities, and finally put the results into a small dashboard.

The rest of this tutorial follows Maya's investigation. The URLs are requests
her reporting UI could make, and the response tables use the following
illustrative `Sale` data:

| region | category code | amount | quantity |
| --- | --- | ---: | ---: |
| north | food | 10 | 1 |
| north | household | 20 | 2 |
| north | transport | 5 | 3 |
| south | food | 30 | 3 |
| south | household | 40 | 4 |
| south | transport | 5 | 2 |
| zero | food | 0 | 0 |

## Give Maya a sales exploration endpoint

Install the package into a Django project that already uses Django REST
Framework:

```bash
pip install drf-cube
```

## Run the included demo

When working from a source checkout, the test project can also serve the same
sales data used by the aggregation tests. It is a development example, not a
component installed into your Django application.

```bash
uv sync
uv run python manage.py migrate --run-syncdb
uv run python manage.py seed_demo
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000/facts/> for the JSON endpoint or
<http://127.0.0.1:8000/reports/facts/> for Cube Explorer. Re-run
`uv run python manage.py seed_demo` whenever you want to restore the seven
deterministic facts; it replaces only the demo's `Category` and `Fact` rows.
The generated `.demo.sqlite3` database is ignored by Git.

## Tested versions

The unit-test matrix covers Python 3.11–3.13; Django 4.2, 5.0–5.2, and 6.1;
and Django REST Framework 3.14–3.18. It runs these compatible version
combinations:

| Python | Django | Django REST Framework |
| --- | --- | --- |
| 3.11 | 4.2 | 3.14, 3.15 |
| 3.12 | 5.0 | 3.15 |
| 3.13 | 5.1 | 3.16 |
| 3.13 | 5.2 | 3.16, 3.17 |
| 3.13 | 6.1 | 3.18 |

The public API is simply:

```python
from drf_cube import AggregatingSerializer, AggregatingViewSet, AggregationScope
```

## Optional Cube Explorer page

`drf-cube` can also render a standalone analyst workspace for an existing cube
endpoint. It does not create another ViewSet or implement aggregation: normal
Django GET forms construct the endpoint query, and the page renders the routed
ViewSet's response on the server.

Add `"drf_cube"` to `INSTALLED_APPS` and enable a Django template backend with
`APP_DIRS=True`. No Django admin installation, Bootstrap dependency, admin URL,
model registration, or staff access is required. Then bind the existing ViewSet
and its router URL name once:

```python
from django.urls import path
from drf_cube.explorer import cube_explorer_page

from .views import SaleCubeViewSet

urlpatterns = [
    path(
        "reports/sales/",
        cube_explorer_page(
            api_url_name="sales-list",
            cube_viewset_class=SaleCubeViewSet,
            title="Sales cube",
            description="Explore grouped sales totals.",
        ),
    ),
]
```

The route is ordinary Django navigation; it is neither an admin route nor an
authorization mechanism. The Explorer invokes the routed API action with the
current request identity, so API permissions, queryset scoping, filtering,
throttles, validation, pagination, and every aggregation remain authoritative.
Its canonical page query is the same query sent to the API. In
permissive mode,
`customer__code__icontains=xyz` is a source filter that narrows every total,
while `slice_by_customer__code__icontains=preferred` affects only comparison
columns. There is no aggregate switch. Its dimension checkboxes omit public
fields that are aggregate-class inputs, avoiding a raw `amount` column beside
`amount_sum_row`; it selects the remaining dimensions by default. Direct API
URLs may still request every `Meta.fields` value.

`AggregatingViewSet` provides an aggregation-only `list()` action. Register it
with a regular DRF router just like another viewset:

```python
# urls.py
from rest_framework.routers import DefaultRouter

from .views import SaleCubeViewSet

router = DefaultRouter()
router.register("sales", SaleCubeViewSet, basename="sales")

urlpatterns = router.urls
```

The examples below therefore use `https://localhost:8000/sales/`. Replace
that host and path with your own API.

## 1. Maya starts with: which regions bring in sales?

Define the dimensions clients may request in `Meta.fields`, then add a Django
aggregate class to `aggregates`.

```python
# views.py
from django.db.models import Sum

from drf_cube import AggregatingSerializer, AggregatingViewSet

from .models import Sale


class SaleCubeSerializer(AggregatingSerializer):
    class Meta:
        model = Sale
        fields = ("region", "category_code", "amount", "quantity")

    aggregates = {"amount": Sum}

    @staticmethod
    def field_to_database_path(field_name):
        return {"category_code": "category__code"}.get(field_name, field_name)


class SaleCubeViewSet(AggregatingViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleCubeSerializer
```

Maya begins with a regional total, so her UI asks for `region` as the only
dimension. `fields` is a comma-separated list of public dimensions and
determines both the response dimensions and Django's `GROUP BY`.

**URL:** `GET https://localhost:8000/sales/?fields=region`

| region | amount_sum_row |
| --- | ---: |
| south | 75.0 |
| north | 35.0 |
| zero | 0.0 |

Maya can immediately see that south leads, north follows, and the new `zero`
region has not booked a sale. `_row` means the aggregate covers every record
in that output row. With no explicit `ordering`, rows are sorted by generated
aggregates descending.

If `fields` is omitted, every public field in `Meta.fields` is selected. That
is useful for a detailed cube, but choosing a small `fields` set is usually the
best place to start.

## 2. Maya asks about the typical sale mix

The regional totals identify where revenue is coming from, but Maya also wants
to know the typical quantity behind a sale. Add `Avg` under a public quantity
name. A key may be different from the model field name; map it when necessary.

```python
from django.db.models import Avg, Sum


class SaleCubeSerializer(AggregatingSerializer):
    class Meta:
        model = Sale
        fields = ("region", "category_code", "amount", "quantity")

    aggregates = {
        "amount": Sum,
        "quantity": Avg,
    }

    @staticmethod
    def field_to_database_path(field_name):
        return {
            "category_code": "category__code",
            "quantity": "quantity",
        }.get(field_name, field_name)
```

**URL:** `GET https://localhost:8000/sales/?fields=region`

| region | amount_sum_row | quantity_avg_row |
| --- | ---: | ---: |
| south | 75.0 | 3.0 |
| north | 35.0 | 2.0 |
| zero | 0.0 | 0.0 |

Maya learns that south has both the largest total and the largest average
quantity. For aggregate *classes*, output names follow
`<public_quantity>_<django_aggregate_name>_row`. Change the aggregate one at
a time to ask the next basic question about the same `amount` field.

### How many sales make up each region?

```python
from django.db.models import Count

aggregates = {"amount": Count}
```

**URL:** `GET https://localhost:8000/sales/?fields=region`

| region | amount_count_row |
| --- | ---: |
| south | 3 |
| north | 3 |
| zero | 1 |

This tells Maya that south's lead comes from three recorded sales, not from a
larger number of transactions. `Count` keeps Django's native integer output.

### How small are the smallest sales?

```python
from django.db.models import Min

aggregates = {"amount": Min}
```

**URL:** `GET https://localhost:8000/sales/?fields=region`

| region | amount_min_row |
| --- | ---: |
| south | 5.0 |
| north | 5.0 |
| zero | 0.0 |

Maya can switch to `Max` in the same place to find each region's largest sale
as `amount_max_row`. Django
aggregate classes including `Sum`, `Avg`, `Count`, `Min`, `Max`, `StdDev`, and
`Variance` are supported as row totals and as slices.

## 3. Maya creates a measure that is not stored on the sale

Maya's next question is about the value weighted by units sold, which is not a
single database column. For a measure like that, pass a preconstructed Django
aggregate expression. This example totals `amount * quantity` within each
region:

```python
from django.db.models import F, FloatField, Sum


class SaleCubeSerializer(AggregatingSerializer):
    class Meta:
        model = Sale
        fields = ("region", "category_code", "amount", "quantity")

    aggregates = {
        "weighted_amount": Sum(
            F("amount") * F("quantity"),
            output_field=FloatField(),
        ),
    }
```

**URL:** `GET https://localhost:8000/sales/?fields=region`

| region | weighted_amount_row |
| --- | ---: |
| south | 260.0 |
| north | 65.0 |
| zero | 0.0 |

The dictionary key supplies the response prefix for an expression, so Maya
gets `weighted_amount_row`, not a name derived from `Sum`. Preconstructed
aggregate-containing expressions support the same row, `_slice`, and
`_slice_percentage_of_row` fields as aggregate classes. If an expression has
its own `filter=`, the selected slice is combined with that filter.

## 4. Maya narrows the investigation

Maya now needs to separate an operational question—"show me south"—from an
analytical one—"show only material totals." `drf-cube` supports this with
normal DRF filter backends, aggregate bounds, and ordering. The examples in
this section use the `Sum` configuration from step 1.

### First, limit the source sales

Keep project-specific filters in a normal DRF filter backend. Here, Maya's
region selector restricts the source queryset before the cube is calculated:

```python
from rest_framework.filters import BaseFilterBackend


class RegionFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        region = request.query_params.get("region")
        return queryset.filter(region=region) if region else queryset


class SaleCubeViewSet(AggregatingViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleCubeSerializer
    filter_backends = [RegionFilterBackend]
```

**URL:** `GET https://localhost:8000/sales/?fields=category_code&region=south`

| category_code | amount_sum_row |
| --- | ---: |
| household | 40.0 |
| food | 30.0 |
| transport | 5.0 |

Maya can now compare the south categories without any other region in the
denominator. Filter backends run before `drf-cube` groups or aggregates. Later,
when a slice is active, that same source filter constrains both the row total
and the slice total.

For trusted internal tools that intentionally expose Django's ORM lookup
syntax, set `permissive_query_parameters = True` on the viewset. Then a
parameter such as `customer__code__icontains=xyz` filters the source queryset,
and `slice_by_customer__code__icontains=xyz` creates an ad-hoc slice. This is
opt-in; see the configuration reference for parameter semantics and safety
considerations.

### Then, keep only regions worth attention

Every available generated aggregate accepts inclusive lower and upper bounds.
To focus a review meeting, Maya retains only regions whose total is at least
40:

**URL:** `GET https://localhost:8000/sales/?fields=region&min_amount_sum_row=40`

| region | amount_sum_row |
| --- | ---: |
| south | 75.0 |

Bounds use the generated field name: `min_amount_sum_row`,
`max_amount_sum_row`, and later `min_amount_sum_slice`. A non-numeric or
non-finite bound returns HTTP 400 instead of being treated as zero.

### Put the results in the order Maya needs

Add DRF's `OrderingFilter` to permit explicit ordering. List allowed database
paths in `ordering_fields` as you normally would.

```python
from rest_framework.filters import OrderingFilter


class SaleCubeViewSet(AggregatingViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleCubeSerializer
    filter_backends = [RegionFilterBackend, OrderingFilter]
    ordering_fields = ("region", "category__code")
```

**URL:** `GET https://localhost:8000/sales/?fields=region&ordering=region`

| region | amount_sum_row |
| --- | ---: |
| north | 35.0 |
| south | 75.0 |
| zero | 0.0 |

Maya's UI might use alphabetical region order for a familiar regional review,
even though the default was largest total first. Use `-region` for descending
order or a comma-separated sequence such as `ordering=-region,category__code`.
Explicit ordering is applied after the cube annotations and replaces the
default aggregate-descending ordering.

## 5. Maya investigates sales composition

Maya knows south is leading, but wants to know whether food drives that lead or
whether it is spread across categories. A slice produces a second total from
only the matching records, plus that total as a percentage of the row total.
Configure the permitted predicate in `slice_fields`; its value is a complete
Django ORM lookup path.

The model-facing path in this example is `category__code`, while the public
dimension remains the friendly name `category_code`:

```python
from django.db.models import Sum


class SaleCubeSerializer(AggregatingSerializer):
    class Meta:
        model = Sale
        fields = ("region", "category_code", "amount", "quantity")

    aggregates = {"amount": Sum}
    slice_fields = {"category_code": "category__code"}

    @staticmethod
    def field_to_database_path(field_name):
        return {"category_code": "category__code"}.get(field_name, field_name)
```

Maya requests the `food` portion of every region:

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_code=food`

| region | amount_sum_row | amount_sum_slice | amount_sum_slice_percentage_of_row |
| --- | ---: | ---: | ---: |
| south | 75.0 | 30.0 | 40.0 |
| north | 35.0 | 10.0 | 28.571429 |
| zero | 0.0 | 0.0 | 0.0 |

Maya sees that food makes up 40% of south's revenue but only about 29% of
north's. The numerator is `amount_sum_slice`; the denominator is
`amount_sum_row`. A zero row total yields a percentage of `0.0`, rather than a
database divide-by-zero result.

### Ask whether a broader mix drives the result

Maya can treat food and household as one combined portfolio by repeating a
named slice parameter. Values within that one slice are ORed:

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_code=food&slice_by_category_code=household`

| region | amount_sum_row | amount_sum_slice |
| --- | ---: | ---: |
| south | 75.0 | 70.0 |
| north | 35.0 | 30.0 |
| zero | 0.0 | 0.0 |

To ask the same composition question specifically of north, add a second
configured slice. Different named slices are ANDed:

```python
slice_fields = {
    "category_code": "category__code",
    "region": "region",
}
```

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_code=food&slice_by_category_code=household&slice_by_region=north`

| region | amount_sum_row | amount_sum_slice |
| --- | ---: | ---: |
| north | 35.0 | 30.0 |
| south | 75.0 | 0.0 |
| zero | 0.0 | 0.0 |

In short: repeated values for one `slice_by_<name>` are ORed; different named
slices are ANDed. That lets Maya build a focused composition question without
changing the backend endpoint.

### Escalate only a meaningful composition finding

Maya might want a list of regions where food sales are exactly 30, or where
food represents at least a chosen share. Slice totals and percentages accept
inclusive aggregate bounds. They require an active `slice_by_...` parameter
because the corresponding annotations do not otherwise exist.

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_code=food&min_amount_sum_slice=30&max_amount_sum_slice=30`

| region | amount_sum_row | amount_sum_slice | amount_sum_slice_percentage_of_row |
| --- | ---: | ---: | ---: |
| south | 75.0 | 30.0 | 40.0 |

She can combine row, slice, and percentage bounds. For example,
`min_amount_sum_row=75&min_amount_sum_slice_percentage_of_row=40` also
selects the `south` row in this data.

## 6. Maya tries a one-off slice without changing the report

Sometimes Maya needs a case-insensitive one-off slice, but does not need the
category in each output row. For a public field that is *not* selected in
`fields`, use `slice_by_<field>__<lookup>`:

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_code__iexact=food`

| region | amount_sum_row | amount_sum_slice |
| --- | ---: | ---: |
| south | 75.0 | 30.0 |
| north | 35.0 | 10.0 |
| zero | 0.0 | 0.0 |

Dynamic lookups are deliberately allowlisted by
`AggregatingSerializer.dynamic_slice_lookups`. The default set includes common
lookups such as `exact`, `iexact`, `contains`, `icontains`, comparisons,
prefix/suffix lookups, dates, and `isnull`. Replace it to narrow the public
API or add an appropriate lookup registered by your project:

```python
class SaleCubeSerializer(AggregatingSerializer):
    dynamic_slice_lookups = frozenset({"exact", "iexact"})
```

An unsupported dynamic lookup returns HTTP 400 with the parameter name and
the supported lookup list. A configured `slice_fields` entry is different: it
already contains the complete ORM lookup expression, so it can express a
purpose-built rule such as `items__product__code__istartswith`.

## 7. Maya drills into the regions that need an explanation

After identifying south as the priority, Maya can request two dimensions to
change the cube's grain and see every region-category combination:

**URL:** `GET https://localhost:8000/sales/?fields=region,category_code&ordering=region,category__code`

| region | category_code | amount_sum_row |
| --- | --- | ---: |
| north | food | 10.0 |
| north | household | 20.0 |
| north | transport | 5.0 |
| south | food | 30.0 |
| south | household | 40.0 |
| south | transport | 5.0 |
| zero | food | 0.0 |

`field_to_database_path()` is the single place to translate stable public
names to model fields or relation traversals. The mapping is used for grouping,
aggregate input fields, custom slices, explicit model-field ordering, and
response values. That makes it practical to publish `category_code` while the
database uses `category__code`, or to choose a localized relation path in the
consumer project.

Unknown values in `fields` are ignored. This lets Maya's UI evolve without
breaking older servers, but clients should still request only dimensions the
endpoint documents.

## 8. Put Maya's exploration into a paginated dashboard

Maya's small example fits in one response, but a real dashboard may expose
many groups. `drf-cube` uses the viewset's ordinary DRF pagination. It does
not impose a page size, which avoids an unnecessary count query on every
aggregation. For an opt-in page-size parameter:

```python
from rest_framework.pagination import PageNumberPagination


class CubePagination(PageNumberPagination):
    page_size = None
    page_size_query_param = "page_size"
    max_page_size = 100


class SaleCubeViewSet(AggregatingViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleCubeSerializer
    pagination_class = CubePagination
```

**URL:** `GET https://localhost:8000/sales/?fields=region&page=1&page_size=2`

| response property | value |
| --- | --- |
| `count` | `3` |
| `results` | the first two grouped rows |

The paginated JSON response follows your DRF pagination class; with the class
above it has the usual `count`, `next`, `previous`, and `results` shape.

## 9. Give Maya the complete exploration tool

The earlier snippets deliberately introduced one idea at a time. In the real
application, the developer puts the full configuration in place once. Maya
never edits this Python: her UI translates its region picker, dimensions,
category controls, thresholds, sorting controls, and page controls into the
query parameters already shown above.

This self-contained example combines the tutorial's analyses. It gives Maya
revenue, average units, a sales count, smallest and largest sale values, and a
weighted value. It also supports the named and dynamic slices,
source-region filtering, explicit ordering, and opt-in pagination used above.

```python
# sales/cube.py
from django.db.models import Avg, Count, F, FloatField, Max, Min, Sum
from rest_framework.filters import BaseFilterBackend, OrderingFilter
from rest_framework.pagination import PageNumberPagination

from drf_cube import AggregatingSerializer, AggregatingViewSet

from .models import Sale


class SalesManagerFilterBackend(BaseFilterBackend):
    """Source filters owned by the application, not by the sales manager."""

    def filter_queryset(self, request, queryset, view):
        region = request.query_params.get("region")
        return queryset.filter(region=region) if region else queryset


class MayaSalesPagination(PageNumberPagination):
    page_size = None
    page_size_query_param = "page_size"
    max_page_size = 100


class MayaSalesCubeSerializer(AggregatingSerializer):
    class Meta:
        model = Sale
        # These are the dimensions Maya may select with ?fields=.
        fields = ("region", "category_code", "amount", "quantity")

    # Each key is a stable, UI-friendly public measure name. Aggregate classes
    # Both entries create row, slice, and slice-percentage fields.
    aggregates = {
        "amount": Sum,
        "quantity": Avg,
        "sales": Count,
        "smallest_amount": Min,
        "largest_amount": Max,
        "weighted_amount": Sum(
            F("amount") * F("quantity"),
            output_field=FloatField(),
        ),
    }

    # These are named, product-approved composition questions.
    slice_fields = {
        "category_code": "category__code",
        "category_prefix": "category__code__istartswith",
        "region": "region",
    }

    # The UI may use these one-off dynamic lookups on a dimension omitted from
    # ?fields=. Add only lookups that the product deliberately exposes.
    dynamic_slice_lookups = frozenset(
        {"exact", "iexact", "contains", "icontains", "startswith", "istartswith"}
    )

    @staticmethod
    def field_to_database_path(field_name):
        return {
            "category_code": "category__code",
            "quantity": "quantity",
            "sales": "pk",
            "smallest_amount": "amount",
            "largest_amount": "amount",
        }.get(field_name, field_name)


class MayaSalesCubeViewSet(AggregatingViewSet):
    queryset = Sale.objects.all()
    serializer_class = MayaSalesCubeSerializer
    filter_backends = [SalesManagerFilterBackend, OrderingFilter]
    # Ordinary dimension ordering that Maya's UI may offer.
    ordering_fields = ("region", "category__code")
    pagination_class = MayaSalesPagination
```

The two important subclasses are `MayaSalesCubeSerializer` and
`MayaSalesCubeViewSet`; the small filter and pagination classes are ordinary
DRF plumbing that the project may replace with its existing equivalents. The
serializer is the developer-controlled catalogue of names and safe query
options. That boundary is what lets a non-programmer explore broadly without
being able to request arbitrary ORM operations.

Once configured, a UI can offer plain-language controls that produce requests
like these:

| Maya's question or control | Request the UI sends |
| --- | --- |
| “Which regions generate revenue?” | `?fields=region` |
| “Show revenue and average units by region.” | `?fields=region` |
| “How much of each region is food?” | `?fields=region&slice_by_category_code=food` |
| “Show only strong food regions.” | `?fields=region&slice_by_category_code=food&min_amount_sum_slice=30` |
| “Look at south's category mix.” | `?fields=category_code&region=south` |
| “Which categories drive each region?” | `?fields=region,category_code&ordering=region,category__code` |
| “Sort regions alphabetically.” | `?fields=region&ordering=region` |
| “Load the next dashboard page.” | `?fields=region&page=2&page_size=25` |

### Combine dimensions for a regional composition report

Maya does not have to choose between a regional report and a category report.
She can request both dimensions, which groups sales by each unique
region-category pair:

**URL:** `GET https://localhost:8000/sales/?fields=region,category_code&ordering=region,category__code`

| region | category_code | amount_sum_row | sales_count_row |
| --- | --- | ---: | ---: |
| north | food | 10.0 | 1 |
| north | household | 20.0 | 1 |
| north | transport | 5.0 | 1 |
| south | food | 30.0 | 1 |
| south | household | 40.0 | 1 |
| south | transport | 5.0 | 1 |
| zero | food | 0.0 | 1 |

The same pattern extends to every public dimension in `Meta.fields`: add it to
the comma-separated `fields` list when Maya needs a more detailed breakdown,
or remove it when she needs a higher-level total.

### Ask more ambitious questions without adding reports

The same two subclasses can answer questions that would otherwise become
separate, hand-built reports. The tables below show only the relevant response
fields; the endpoint still returns the other configured measures too.

#### Where is the core portfolio both large and dominant?

Maya defines her core portfolio as `food` **or** `household`. She wants regions
with at least 50 in total sales where that portfolio makes up at least 80% of
the region's sales:

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_code=food&slice_by_category_code=household&min_amount_sum_row=50&min_amount_sum_slice_percentage_of_row=80&ordering=-amount_sum_row`

| region | total sales | core-portfolio sales | core-portfolio share |
| --- | ---: | ---: | ---: |
| south | 75.0 | 70.0 | 93.333333% |

One repeated slice parameter creates the portfolio's OR condition; the two
aggregate bounds turn Maya's business rule into an API request. No endpoint
or SQL report was added.

#### Which high-value regions have little transport exposure?

Maya is planning a promotion and wants significant regions where transport is
no more than 10% of sales. This combines a row-total floor with an upper bound
on a sliced percentage:

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_code=transport&min_amount_sum_row=50&max_amount_sum_slice_percentage_of_row=10&ordering=-amount_sum_row`

| region | total sales | transport sales | transport share |
| --- | ---: | ---: | ---: |
| south | 75.0 | 5.0 | 6.666667% |

That is a useful shortlist: it identifies a sizable market whose current mix
has low transport exposure, without loading every sale into the dashboard.

#### Which unit quantities make revenue in each region?

Maya can change the grain again, this time to inspect both region and quantity
at once. Explicit ordering makes the result read like a small pivot table:

**URL:** `GET https://localhost:8000/sales/?fields=region,quantity&ordering=region,quantity`

| region | quantity | amount_sum_row | sales_count_row |
| --- | ---: | ---: | ---: |
| north | 1 | 10.0 | 1 |
| north | 2 | 20.0 | 1 |
| north | 3 | 5.0 | 1 |
| south | 2 | 5.0 | 1 |
| south | 3 | 30.0 | 1 |
| south | 4 | 40.0 | 1 |
| zero | 0 | 0.0 | 1 |

The `fields` list changes the grouping level; it does not require a different
viewset. With real dimensions such as salesperson, channel, product family, or
month added to `Meta.fields`, the exact same control can drill into those too.

#### Can the dashboard explore a new category naming convention safely?

The configured `category_prefix` slice gives Maya a friendly control for a
case-insensitive category prefix. She can ask for material categories beginning
with “HOU” without exposing an arbitrary ORM lookup:

**URL:** `GET https://localhost:8000/sales/?fields=region&slice_by_category_prefix=HOU&min_amount_sum_slice=25&ordering=-amount_sum_slice`

| region | total sales | matching category sales |
| --- | ---: | ---: |
| south | 75.0 | 40.0 |

This is the balance the simple setup provides: broad exploration for Maya, and
a small, reviewable allowlist of dimensions, measures, and predicates for the
developer.

With the complete serializer, each response includes the configured generated
measures: `amount_sum_row`, `quantity_avg_row`, `sales_count_row`,
`smallest_amount_min_row`, `largest_amount_max_row`, and
`weighted_amount_row`. When a slice is active, every configured aggregate
measure also gains its `_slice` and `_slice_percentage_of_row` variants.

To add a future sales question, a developer normally makes one local change:
add a public aggregate, dimension mapping, named slice, or application filter.
The UI can then expose the new capability as another ordinary control rather
than needing a new endpoint or a custom report for every question.

## License

MIT. See [LICENSE](LICENSE).
