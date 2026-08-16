"""Regression coverage for the implemented decisions 6–9."""

from typing import ClassVar

from django.db.models import (
    Avg,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Min,
    Q,
    StdDev,
    Sum,
    Variance,
)
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from .factories import create_facts
from .testapp.cube import (
    ExpressionFactCubeViewSet,
    FactCubeSerializer,
    FactCubeViewSet,
)


class DecisionCoverageTests(TestCase):
    def setUp(self):
        create_facts()

    def get_rows(self, query):
        response = self.client.get(f"/facts/{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def get_viewset_rows(self, viewset_class, query):
        request = APIRequestFactory().get(f"/facts/{query}")
        response = viewset_class.as_view({"get": "list"})(request)
        response.render()
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    @staticmethod
    def viewset_for_aggregate(aggregate):
        class AggregateSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {"amount": aggregate}

        class AggregateViewSet(FactCubeViewSet):
            serializer_class = AggregateSerializer

        return AggregateViewSet

    def test_row_and_percentage_bounds_are_inclusive(self):
        rows = self.get_rows(
            "?fields=region&slice_by_category_code=food"
            "&min_amount_sum_row=35"
            "&min_amount_sum_slice_percentage_of_row=40"
        )

        self.assertEqual([row["region"] for row in rows], ["south"])

    def test_sum_and_avg_are_supported(self):
        row = self.get_rows("?fields=region&slice_by_category_code=food")[0]

        self.assertIn("amount_sum_slice", row)
        self.assertIn("quantity_avg_slice", row)

    def test_model_field_ordering_overrides_default_aggregate_ordering(self):
        rows = self.get_rows("?fields=region&ordering=region")

        self.assertEqual([row["region"] for row in rows], ["north", "south", "zero"])

    def test_multiple_model_field_ordering_is_supported(self):
        rows = self.get_rows(
            "?fields=region,category_code&ordering=-region,category__code"
        )

        self.assertEqual(
            [(row["region"], row["category_code"]) for row in rows],
            [
                ("zero", "food"),
                ("south", "food"),
                ("south", "household"),
                ("south", "transport"),
                ("north", "food"),
                ("north", "household"),
                ("north", "transport"),
            ],
        )

    def test_unsupported_dynamic_slice_lookup_returns_a_400(self):
        """Decision 6: reject dynamic slice lookups outside the allowlist."""
        response = self.client.get(
            "/facts/?fields=region&slice_by_category_code__unsupported=food"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Unsupported dynamic slice lookup 'unsupported'",
            response.json()["slice_by_category_code__unsupported"],
        )

    def test_dynamic_slice_lookup_allowlist_can_be_overridden(self):
        """Decision 6: serializers can narrow the dynamic lookup allowlist."""

        class RestrictedSerializer(FactCubeSerializer):
            dynamic_slice_lookups = frozenset()

        class RestrictedViewSet(FactCubeViewSet):
            serializer_class = RestrictedSerializer

        request = APIRequestFactory().get(
            "/facts/?fields=region&slice_by_category_code__iexact=food"
        )
        response = RestrictedViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Supported lookups:",
            response.data["slice_by_category_code__iexact"],
        )

    def test_row_bounds_work_without_a_slice_and_are_inclusive(self):
        """Decision 7: row annotations must be filtered before any slice exists."""
        rows = self.get_rows(
            "?fields=region&min_amount_sum_row=35&max_amount_sum_row=35"
        )

        self.assertEqual([row["region"] for row in rows], ["north"])

    def test_slice_total_bounds_are_inclusive(self):
        """Decision 7: direct slice totals support inclusive min and max bounds."""
        rows = self.get_rows(
            "?fields=region&slice_by_category_code=food"
            "&min_amount_sum_slice=30&max_amount_sum_slice=30"
        )

        self.assertEqual([row["region"] for row in rows], ["south"])

    def test_row_slice_and_percentage_bounds_can_be_combined(self):
        """Decision 7: all generated bounds are ANDed after their annotations."""
        rows = self.get_rows(
            "?fields=region&region=south&slice_by_category_code=food"
            "&min_amount_sum_row=75&max_amount_sum_row=75"
            "&min_amount_sum_slice=30&max_amount_sum_slice=30"
            "&min_amount_sum_slice_percentage_of_row=40"
            "&max_amount_sum_slice_percentage_of_row=40"
        )

        self.assertEqual([row["region"] for row in rows], ["south"])

    def test_slice_specific_bounds_without_a_slice_return_a_400(self):
        """Decision 7: unavailable annotations cannot be silently ignored."""
        for parameter_name in (
            "min_amount_sum_slice",
            "max_amount_sum_slice_percentage_of_row",
        ):
            with self.subTest(parameter_name=parameter_name):
                response = self.client.get(f"/facts/?fields=region&{parameter_name}=30")

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()[parameter_name],
                    "This bound requires an active slice selected with a "
                    "slice_by_ parameter.",
                )

    def test_invalid_aggregate_bound_returns_a_400(self):
        """Decision 6: malformed numeric query parameters must not become 500s."""
        response = self.client.get(
            "/facts/?fields=region&slice_by_category_code=food&min_amount_sum_row=nope"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["min_amount_sum_row"],
            "Enter a finite number, for example 12.5.",
        )

    def test_builtin_aggregate_classes_support_row_and_slice_matrix(self):
        """Decision 8: the documented Django aggregate-class matrix is covered."""
        for aggregate in (Sum, Avg, Count, Min, Max, StdDev, Variance):
            with self.subTest(aggregate=aggregate.__name__):
                viewset_class = self.viewset_for_aggregate(aggregate)
                aggregate_name = aggregate.name.lower()
                row_name = f"amount_{aggregate_name}_row"
                slice_name = f"amount_{aggregate_name}_slice"
                percentage_name = f"{slice_name}_percentage_of_row"

                row_rows = {
                    row["region"]: row
                    for row in self.get_viewset_rows(viewset_class, "?fields=region")
                }
                north_row = row_rows["north"]
                self.assertIn(row_name, north_row)

                slice_rows = {
                    row["region"]: row
                    for row in self.get_viewset_rows(
                        viewset_class,
                        "?fields=region&slice_by_category_code=food",
                    )
                }
                north_slice = slice_rows["north"]
                self.assertIn(slice_name, north_slice)
                self.assertIn(percentage_name, north_slice)

                expected_row = FactCubeSerializer.Meta.model.objects.filter(
                    region="north"
                ).aggregate(value=aggregate("amount"))["value"]
                expected_slice = FactCubeSerializer.Meta.model.objects.filter(
                    region="north", category__code="food"
                ).aggregate(value=aggregate("amount"))["value"]
                self.assertAlmostEqual(north_row[row_name], expected_row)
                self.assertAlmostEqual(north_slice[slice_name], expected_slice)
                self.assertAlmostEqual(
                    north_slice[percentage_name], expected_slice * 100 / expected_row
                )

                no_match_rows = {
                    row["region"]: row
                    for row in self.get_viewset_rows(
                        viewset_class,
                        "?fields=region&slice_by_category_code=missing",
                    )
                }
                self.assertEqual(no_match_rows["north"][slice_name], 0)

                bounded_rows = self.get_viewset_rows(
                    viewset_class,
                    "?fields=region&slice_by_category_code=food"
                    f"&min_{slice_name}={expected_slice}",
                )
                self.assertIn("north", {row["region"] for row in bounded_rows})

    def test_count_uses_integer_output_and_supports_slice_bounds(self):
        """Decision 8: Count has no synthetic default or forced float output."""
        count_viewset = self.viewset_for_aggregate(Count)
        rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                count_viewset,
                "?fields=region&slice_by_category_code=food",
            )
        }

        self.assertEqual(rows["north"]["amount_count_row"], 3)
        self.assertIsInstance(rows["north"]["amount_count_row"], int)
        self.assertEqual(rows["north"]["amount_count_slice"], 1)
        self.assertEqual(
            self.get_viewset_rows(
                count_viewset,
                "?fields=region&slice_by_category_code=missing",
            )[0]["amount_count_slice"],
            0,
        )

        bounded_rows = self.get_viewset_rows(
            count_viewset,
            "?fields=region&slice_by_category_code=food"
            "&min_amount_count_row=3&max_amount_count_row=3"
            "&min_amount_count_slice=1&max_amount_count_slice=1",
        )
        self.assertEqual({row["region"] for row in bounded_rows}, {"north", "south"})

    def test_custom_aggregate_supports_named_and_dynamic_slices(self):
        """A custom aggregate has the full generated-field contract."""
        for query in (
            "?fields=region&slice_by_category_code=food",
            "?fields=region&slice_by_category_code__iexact=food",
        ):
            with self.subTest(query=query):
                rows = {
                    row["region"]: row
                    for row in self.get_viewset_rows(ExpressionFactCubeViewSet, query)
                }
                north_row = rows["north"]
                self.assertEqual(north_row["weighted_amount_row"], 65.0)
                self.assertEqual(north_row["weighted_amount_slice"], 10.0)
                self.assertAlmostEqual(
                    north_row["weighted_amount_slice_percentage_of_row"], 200 / 13
                )
                self.assertEqual(rows["south"]["weighted_amount_slice"], 90.0)
                self.assertEqual(rows["zero"]["weighted_amount_slice"], 0.0)
                self.assertEqual(
                    rows["zero"]["weighted_amount_slice_percentage_of_row"], 0.0
                )

        rows = self.get_viewset_rows(
            ExpressionFactCubeViewSet,
            "?fields=region&region=north"
            "&slice_by_category_code=food&slice_by_category_code=household"
            "&slice_by_region=north",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["weighted_amount_row"], 65.0)
        self.assertEqual(rows[0]["weighted_amount_slice"], 50.0)
        self.assertAlmostEqual(
            rows[0]["weighted_amount_slice_percentage_of_row"], 1000 / 13
        )

    def test_mixed_class_and_custom_aggregates_share_a_slice(self):
        """Class and custom aggregates can be sliced in one grouped query."""
        configured_aggregate = Sum(
            F("amount") * F("quantity"), output_field=FloatField()
        )

        class MixedSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {
                "amount": Sum,
                "weighted_amount": configured_aggregate,
            }

        class MixedViewSet(FactCubeViewSet):
            serializer_class = MixedSerializer

        rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                MixedViewSet,
                "?fields=region&slice_by_category_code=food",
            )
        }

        north_row = rows["north"]
        self.assertEqual(north_row["amount_sum_slice"], 10.0)
        self.assertEqual(north_row["weighted_amount_row"], 65.0)
        self.assertEqual(north_row["weighted_amount_slice"], 10.0)
        self.assertAlmostEqual(
            north_row["weighted_amount_slice_percentage_of_row"], 200 / 13
        )

    def test_custom_aggregate_requests_do_not_leak_slice_state(self):
        """Each request gets its own sliced clone of the configured aggregate."""
        configured_aggregate = Sum(
            F("amount") * F("quantity"), output_field=FloatField()
        )

        class StatefulSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {
                "weighted_amount": configured_aggregate,
            }

        class StatefulViewSet(FactCubeViewSet):
            serializer_class = StatefulSerializer

        for slice_value, expected_slice in (("food", 10.0), ("household", 40.0)):
            rows = self.get_viewset_rows(
                StatefulViewSet,
                f"?fields=region&slice_by_category_code={slice_value}",
            )
            north_row = next(row for row in rows if row["region"] == "north")
            self.assertEqual(north_row["weighted_amount_row"], 65.0)
            self.assertEqual(north_row["weighted_amount_slice"], expected_slice)

        self.assertIsNone(configured_aggregate.filter)

    def test_custom_aggregate_slice_bounds_require_a_slice(self):
        """Custom generated slice fields follow the same bound validation."""
        for parameter_name in (
            "min_weighted_amount_slice",
            "max_weighted_amount_slice_percentage_of_row",
        ):
            with self.subTest(parameter_name=parameter_name):
                request = APIRequestFactory().get(
                    f"/facts/?fields=region&{parameter_name}=1"
                )
                response = ExpressionFactCubeViewSet.as_view({"get": "list"})(request)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.data[parameter_name],
                    "This bound requires an active slice selected with a "
                    "slice_by_ parameter.",
                )

        request = APIRequestFactory().get(
            "/facts/?fields=region&slice_by_category_code=food"
            "&min_weighted_amount_slice=not-a-number"
        )
        response = ExpressionFactCubeViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["min_weighted_amount_slice"],
            "Enter a finite number, for example 12.5.",
        )

    def test_custom_aggregate_row_slice_and_percentage_bounds_are_inclusive(self):
        """All custom aggregate annotations can be bounded together."""
        rows = self.get_viewset_rows(
            ExpressionFactCubeViewSet,
            "?fields=region&slice_by_category_code=food"
            "&min_weighted_amount_row=65&max_weighted_amount_row=65"
            "&min_weighted_amount_slice=10&max_weighted_amount_slice=10"
            "&min_weighted_amount_slice_percentage_of_row=15.384615"
            "&max_weighted_amount_slice_percentage_of_row=15.384616",
        )

        self.assertEqual([row["region"] for row in rows], ["north"])

    def test_custom_aggregate_default_order_and_explicit_generated_ordering(self):
        """A custom-only cube orders by its slice, row, and percentage fields."""
        cases = (
            ("", ["south", "north", "zero"]),
            ("&ordering=weighted_amount_row", ["zero", "north", "south"]),
            ("&ordering=-weighted_amount_slice", ["south", "north", "zero"]),
            (
                "&ordering=weighted_amount_slice_percentage_of_row",
                ["zero", "north", "south"],
            ),
        )
        for ordering, expected_regions in cases:
            with self.subTest(ordering=ordering):
                rows = self.get_viewset_rows(
                    ExpressionFactCubeViewSet,
                    "?fields=region&slice_by_category_code=food" + ordering,
                )
                self.assertEqual([row["region"] for row in rows], expected_regions)

    def test_custom_aggregate_preserves_and_intersects_its_configured_filter(self):
        """The configured aggregate filter is ANDed with each selected slice."""
        configured_aggregate = Sum(
            F("amount") * F("quantity"),
            filter=Q(quantity__gte=2),
            default=0,
            output_field=FloatField(),
        )

        class FilteredExpressionSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {
                "large_weighted_amount": configured_aggregate,
            }

        class FilteredExpressionViewSet(FactCubeViewSet):
            serializer_class = FilteredExpressionSerializer

        food_rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                FilteredExpressionViewSet,
                "?fields=region&slice_by_category_code=food",
            )
        }
        self.assertEqual(food_rows["north"]["large_weighted_amount_row"], 55.0)
        self.assertEqual(food_rows["north"]["large_weighted_amount_slice"], 0.0)
        self.assertEqual(food_rows["south"]["large_weighted_amount_slice"], 90.0)
        self.assertEqual(food_rows["zero"]["large_weighted_amount_slice"], 0.0)

        household_rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                FilteredExpressionViewSet,
                "?fields=region&slice_by_category_code=household",
            )
        }
        self.assertEqual(household_rows["north"]["large_weighted_amount_slice"], 40.0)
        self.assertEqual(household_rows["south"]["large_weighted_amount_slice"], 160.0)
        self.assertIn("quantity__gte", repr(configured_aggregate.filter))
        self.assertNotIn("category__code", repr(configured_aggregate.filter))

    def test_nested_custom_expression_slices_each_aggregate_without_mutation(self):
        """Wrapping or combining aggregates does not leave an unsliced child."""
        configured_expression = ExpressionWrapper(
            Sum(F("amount")) + Sum(F("quantity")), output_field=FloatField()
        )

        class NestedExpressionSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {
                "amount_plus_quantity": configured_expression,
            }

        class NestedExpressionViewSet(FactCubeViewSet):
            serializer_class = NestedExpressionSerializer

        rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                NestedExpressionViewSet,
                "?fields=region&slice_by_category_code=food",
            )
        }
        self.assertEqual(rows["north"]["amount_plus_quantity_row"], 41.0)
        self.assertEqual(rows["north"]["amount_plus_quantity_slice"], 11.0)
        self.assertAlmostEqual(
            rows["north"]["amount_plus_quantity_slice_percentage_of_row"],
            1100 / 41,
        )
        self.assertEqual(rows["south"]["amount_plus_quantity_slice"], 33.0)

        child_aggregates = configured_expression.get_source_expressions()[
            0
        ].get_source_expressions()
        self.assertTrue(all(aggregate.filter is None for aggregate in child_aggregates))

    def test_custom_aggregate_preserves_configured_empty_slice_result_semantics(self):
        """A custom expression's default, rather than cube code, controls no-match values."""
        nullable_aggregate = Sum(F("amount") * F("quantity"), output_field=FloatField())
        defaulted_aggregate = Sum(
            F("amount") * F("quantity"), default=0, output_field=FloatField()
        )

        class DefaultExpressionSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {
                "nullable_weighted_amount": nullable_aggregate,
                "defaulted_weighted_amount": defaulted_aggregate,
            }

        class DefaultExpressionViewSet(FactCubeViewSet):
            serializer_class = DefaultExpressionSerializer

        rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                DefaultExpressionViewSet,
                "?fields=region&slice_by_category_code=missing",
            )
        }
        north_row = rows["north"]
        self.assertIsNone(north_row["nullable_weighted_amount_slice"])
        self.assertEqual(north_row["defaulted_weighted_amount_slice"], 0.0)
        self.assertEqual(
            north_row["nullable_weighted_amount_slice_percentage_of_row"], 0.0
        )
        self.assertEqual(
            north_row["defaulted_weighted_amount_slice_percentage_of_row"], 0.0
        )

    def test_custom_aggregate_percentage_handles_signed_row_and_slice_values(self):
        """Percentages retain their sign for values on opposite sides of zero."""
        category_model = FactCubeSerializer.Meta.model._meta.get_field(
            "category"
        ).remote_field.model
        FactCubeSerializer.Meta.model.objects.bulk_create(
            [
                FactCubeSerializer.Meta.model(
                    category=category_model.objects.get(code="food"),
                    region="opposed",
                    amount=10,
                    quantity=1,
                ),
                FactCubeSerializer.Meta.model(
                    category=category_model.objects.get(code="household"),
                    region="opposed",
                    amount=-20,
                    quantity=1,
                ),
                FactCubeSerializer.Meta.model(
                    category=category_model.objects.get(code="food"),
                    region="negative",
                    amount=-10,
                    quantity=1,
                ),
                FactCubeSerializer.Meta.model(
                    category=category_model.objects.get(code="household"),
                    region="negative",
                    amount=-10,
                    quantity=1,
                ),
            ]
        )

        rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                ExpressionFactCubeViewSet,
                "?fields=region&slice_by_category_code=food",
            )
        }
        self.assertEqual(rows["opposed"]["weighted_amount_row"], -10.0)
        self.assertEqual(rows["opposed"]["weighted_amount_slice"], 10.0)
        self.assertEqual(
            rows["opposed"]["weighted_amount_slice_percentage_of_row"], -100.0
        )
        self.assertEqual(rows["negative"]["weighted_amount_row"], -20.0)
        self.assertEqual(rows["negative"]["weighted_amount_slice"], -10.0)
        self.assertEqual(
            rows["negative"]["weighted_amount_slice_percentage_of_row"], 50.0
        )

    def test_custom_aggregate_percentage_is_zero_when_its_filter_excludes_everything(
        self,
    ):
        """A null row and null slice are safely rendered as a zero percentage."""
        excluded_aggregate = Sum(
            F("amount") * F("quantity"),
            filter=Q(quantity__gt=99),
            output_field=FloatField(),
        )

        class ExcludedExpressionSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {
                "excluded_weighted_amount": excluded_aggregate,
            }

        class ExcludedExpressionViewSet(FactCubeViewSet):
            serializer_class = ExcludedExpressionSerializer

        rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                ExcludedExpressionViewSet,
                "?fields=region&slice_by_category_code=food",
            )
        }
        north_row = rows["north"]
        self.assertIsNone(north_row["excluded_weighted_amount_row"])
        self.assertIsNone(north_row["excluded_weighted_amount_slice"])
        self.assertEqual(
            north_row["excluded_weighted_amount_slice_percentage_of_row"], 0.0
        )
        self.assertIn("quantity__gt", repr(excluded_aggregate.filter))
        self.assertNotIn("category__code", repr(excluded_aggregate.filter))

    def test_preconstructed_count_preserves_distinct_and_integer_slice_results(self):
        """Instance configuration other than the aggregate input is retained."""
        configured_count = Count("category", distinct=True)

        class CountExpressionSerializer(FactCubeSerializer):
            aggregates: ClassVar[dict[str, object]] = {"categories": configured_count}

        class CountExpressionViewSet(FactCubeViewSet):
            serializer_class = CountExpressionSerializer

        rows = {
            row["region"]: row
            for row in self.get_viewset_rows(
                CountExpressionViewSet,
                "?fields=region&slice_by_category_code=food",
            )
        }
        self.assertEqual(rows["north"]["categories_row"], 3)
        self.assertIsInstance(rows["north"]["categories_slice"], int)
        self.assertEqual(rows["north"]["categories_slice"], 1)
        self.assertEqual(rows["zero"]["categories_slice"], 1)
        self.assertTrue(configured_count.distinct)
        self.assertIsNone(configured_count.filter)

    def test_generated_aggregate_ordering_is_applied_after_annotation(self):
        """Decision 9: generated aggregate ordering runs after annotation."""
        rows = self.get_rows("?fields=region&ordering=amount_sum_row")

        self.assertEqual([row["region"] for row in rows], ["zero", "north", "south"])
