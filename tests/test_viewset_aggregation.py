from django.test import TestCase
from rest_framework.test import APIRequestFactory

from .factories import create_facts
from .testapp.cube import ExpressionFactCubeViewSet, PermissiveFactCubeViewSet


class AggregatingViewSetTests(TestCase):
    def setUp(self):
        create_facts()

    def get_rows(self, query=""):
        response = self.client.get(f"/facts/{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_fields_control_grouping_and_default_ordering(self):
        rows = self.get_rows("?fields=region")

        self.assertEqual([row["region"] for row in rows], ["south", "north", "zero"])
        self.assertEqual([row["amount_sum_row"] for row in rows], [75.0, 35.0, 0.0])

    def test_aggregate_classes_are_calculated(self):
        rows = {row["region"]: row for row in self.get_rows("?fields=region")}

        self.assertEqual(rows["north"]["amount_sum_row"], 35.0)
        self.assertAlmostEqual(rows["north"]["quantity_avg_row"], 2.0)

    def test_constructed_expression_is_calculated_without_a_slice(self):
        request = APIRequestFactory().get("/facts/?fields=region")
        response = ExpressionFactCubeViewSet.as_view({"get": "list"})(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        rows = {row["region"]: row for row in response.data}
        self.assertEqual(rows["north"]["weighted_amount_row"], 65.0)

    def test_global_filter_applies_to_row_and_slice_totals(self):
        rows = self.get_rows("?fields=region&region=north&slice_by_category_code=food")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["region"], "north")
        self.assertEqual(row["amount_sum_row"], 35.0)
        self.assertEqual(row["amount_sum_slice"], 10.0)
        self.assertAlmostEqual(row["amount_sum_slice_percentage_of_row"], 200 / 7)

    def test_named_slice_values_are_ored_and_distinct_slices_are_anded(self):
        rows = self.get_rows(
            "?fields=region"
            "&slice_by_category_code=food"
            "&slice_by_category_code=household"
            "&slice_by_region=north"
        )
        rows_by_region = {row["region"]: row for row in rows}

        self.assertEqual(rows_by_region["north"]["amount_sum_slice"], 30.0)
        self.assertAlmostEqual(
            rows_by_region["north"]["amount_sum_slice_percentage_of_row"],
            600 / 7,
        )
        self.assertEqual(rows_by_region["south"]["amount_sum_slice"], 0.0)

    def test_custom_slice_lookup_on_an_omitted_public_field(self):
        rows = {
            row["region"]: row
            for row in self.get_rows(
                "?fields=region&slice_by_category_code__iexact=food"
            )
        }

        self.assertEqual(rows["north"]["amount_sum_slice"], 10.0)
        self.assertEqual(rows["south"]["amount_sum_slice"], 30.0)

    def test_zero_denominator_returns_zero_percentage(self):
        rows = {
            row["region"]: row
            for row in self.get_rows("?fields=region&slice_by_category_code=food")
        }

        self.assertEqual(rows["zero"]["amount_sum_row"], 0.0)
        self.assertEqual(rows["zero"]["amount_sum_slice"], 0.0)
        self.assertEqual(rows["zero"]["amount_sum_slice_percentage_of_row"], 0.0)

    def test_pagination_is_left_to_the_consumer_configuration(self):
        response = self.client.get("/facts/?fields=region&page=1&page_size=2")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 3)
        self.assertEqual(len(response.json()["results"]), 2)

    def get_permissive_rows(self, query=""):
        request = APIRequestFactory().get(f"/facts/{query}")
        response = PermissiveFactCubeViewSet.as_view({"get": "list"})(request)
        response.render()
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def test_permissive_source_relation_filter_constrains_all_aggregates(self):
        rows = {
            row["region"]: row
            for row in self.get_permissive_rows(
                "?fields=region&category__code__icontains=food"
                "&slice_by_category__code__icontains=food"
            )
        }

        self.assertEqual(rows["north"]["amount_sum_row"], 10.0)
        self.assertEqual(rows["north"]["quantity_avg_row"], 1.0)
        self.assertEqual(rows["north"]["amount_sum_slice"], 10.0)
        self.assertEqual(rows["south"]["amount_sum_row"], 30.0)

    def test_registered_permissive_endpoint_applies_source_relation_filters(self):
        response = self.client.get(
            "/permissive-facts/?fields=category_code"
            "&category__code__icontains=food"
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            [row["category_code"] for row in response.json()], ["food"]
        )

    def test_permissive_source_filter_requires_explicit_opt_in(self):
        rows = {
            row["region"]: row
            for row in self.get_rows("?fields=region&category__code__icontains=food")
        }

        self.assertEqual(rows["north"]["amount_sum_row"], 35.0)
        self.assertEqual(rows["south"]["amount_sum_row"], 75.0)

    def test_permissive_repeated_source_values_are_ored(self):
        rows = {
            row["region"]: row
            for row in self.get_permissive_rows(
                "?fields=region&category__code=food&category__code=household"
            )
        }

        self.assertEqual(rows["north"]["amount_sum_row"], 30.0)
        self.assertEqual(rows["south"]["amount_sum_row"], 70.0)

    def test_permissive_in_and_isnull_values_follow_url_conventions(self):
        rows = {
            row["region"]: row
            for row in self.get_permissive_rows(
                "?fields=region&category__code__in=food,household"
                "&category__code__isnull=false"
            )
        }

        self.assertEqual(rows["north"]["amount_sum_row"], 30.0)
        self.assertEqual(rows["south"]["amount_sum_row"], 70.0)

    def test_permissive_slice_relation_does_not_constrain_row_totals(self):
        rows = {
            row["region"]: row
            for row in self.get_permissive_rows(
                "?fields=region&slice_by_category__label__icontains=food"
            )
        }

        self.assertEqual(rows["north"]["amount_sum_row"], 35.0)
        self.assertEqual(rows["north"]["amount_sum_slice"], 10.0)
        self.assertEqual(rows["south"]["amount_sum_row"], 75.0)
        self.assertEqual(rows["south"]["amount_sum_slice"], 30.0)

    def test_permissive_mode_preserves_cube_and_pagination_parameters(self):
        request = APIRequestFactory().get(
            "/facts/?fields=region&ordering=region&page=1&page_size=2"
        )
        response = PermissiveFactCubeViewSet.as_view({"get": "list"})(request)
        response.render()

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(
            [row["region"] for row in response.data["results"]], ["north", "south"]
        )

    def test_invalid_permissive_source_and_slice_parameters_return_400(self):
        for parameter_name in (
            "category__missing__icontains=food",
            "slice_by_category__missing__icontains=food",
        ):
            with self.subTest(parameter_name=parameter_name):
                request = APIRequestFactory().get(f"/facts/?fields=region&{parameter_name}")
                response = PermissiveFactCubeViewSet.as_view({"get": "list"})(request)

                self.assertEqual(response.status_code, 400)
                self.assertIn("Invalid ORM filter", response.data[parameter_name.split("=")[0]])

    def test_permissive_parameter_exclusions_leave_application_parameters_alone(self):
        class ExcludingViewSet(PermissiveFactCubeViewSet):
            permissive_query_parameter_exclusions = frozenset({"ui_state"})

        request = APIRequestFactory().get("/facts/?fields=region&ui_state=expanded")
        response = ExcludingViewSet.as_view({"get": "list"})(request)
        response.render()

        self.assertEqual(response.status_code, 200, response.content)
