"""Contract tests for the fully server-rendered optional Cube Explorer page."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from drf_cube.explorer import (
    CubeExplorerMetadata,
    api_query_from_controls,
    cube_explorer_form,
    predicate_formsets,
)

from .factories import create_facts
from .testapp.cube import FactCubeViewSet, PermissiveFactCubeViewSet


class CubeExplorerMetadataTests(SimpleTestCase):
    def test_metadata_comes_from_public_viewset_configuration(self):
        metadata = CubeExplorerMetadata.from_viewset(FactCubeViewSet)

        self.assertEqual(metadata.dimensions, ("region", "category_code"))
        self.assertIn("amount_sum_row", metadata.aggregates)
        self.assertIn("amount_sum_slice_percentage_of_row", metadata.aggregates)
        self.assertEqual(metadata.configured_slices, ("category_code", "region"))
        self.assertFalse(metadata.permissive)
        self.assertIn("category__code", metadata.ordering_fields)
        self.assertIn("fields", metadata.reserved_parameters)
        self.assertEqual(metadata.page_parameter, "page")

    def test_forms_hydrate_a_canonical_api_query(self):
        request = self.client.get(
            "/reports/permissive-facts/?fields=region,category_code"
            "&slice_by_category_code=food&slice_by_category_code=household"
            "&category__code__icontains=food&category__code__icontains=home"
            "&slice_by_customer__code__icontains=preferred"
            "&min_amount_sum_row=10&ordering=-amount_sum_row"
        ).wsgi_request
        metadata = CubeExplorerMetadata.from_viewset(PermissiveFactCubeViewSet)
        form = cube_explorer_form(metadata, request.GET)
        source, slices = predicate_formsets(metadata, request.GET)

        self.assertEqual(form.initial["dimensions"], ["region", "category_code"])
        self.assertEqual(form.initial["slice_by_category_code"], "food,household")
        self.assertEqual(form.initial["min_amount_sum_row"], "10")
        self.assertEqual(form.initial["ordering"], "-amount_sum_row")
        self.assertEqual(
            source.initial[0], {"lookup": "category__code__icontains", "value": "food"}
        )
        self.assertEqual(
            slices.initial[0],
            {"lookup": "customer__code__icontains", "value": "preferred"},
        )

    def test_predicate_reserved_names_are_server_side_only(self):
        metadata = CubeExplorerMetadata.from_viewset(PermissiveFactCubeViewSet)
        source_set, slice_set = predicate_formsets(metadata)

        self.assertFalse(
            source_set.form({"lookup": "fields", "value": "region"}).is_valid()
        )
        self.assertFalse(
            slice_set.form({"lookup": "slice_by_region", "value": "north"}).is_valid()
        )

    def test_valid_controls_serialize_repeated_predicates_on_the_server(self):
        metadata = CubeExplorerMetadata.from_viewset(PermissiveFactCubeViewSet)
        data = {
            "dimensions": ["region"],
            "slice_by_category_code": "food,household",
            "ordering": "-amount_sum_row",
            "source-TOTAL_FORMS": "2",
            "source-INITIAL_FORMS": "0",
            "source-MIN_NUM_FORMS": "0",
            "source-MAX_NUM_FORMS": "20",
            "source-0-lookup": "category__code__icontains",
            "source-0-value": "food",
            "source-1-lookup": "category__code__icontains",
            "source-1-value": "household",
            "slice-TOTAL_FORMS": "1",
            "slice-INITIAL_FORMS": "0",
            "slice-MIN_NUM_FORMS": "0",
            "slice-MAX_NUM_FORMS": "20",
            "slice-0-lookup": "customer__code__icontains",
            "slice-0-value": "preferred",
        }
        form = cube_explorer_form(metadata, data=data)
        source, slices = predicate_formsets(metadata, data=data)

        self.assertTrue(form.is_valid())
        self.assertTrue(source.is_valid())
        self.assertTrue(slices.is_valid())
        query = api_query_from_controls(metadata, form, source, slices)

        self.assertEqual(query["fields"], "region")
        self.assertEqual(query.getlist("slice_by_category_code"), ["food", "household"])
        self.assertEqual(
            query.getlist("category__code__icontains"), ["food", "household"]
        )
        self.assertEqual(
            query.getlist("slice_by_customer__code__icontains"), ["preferred"]
        )


class CubeExplorerPageTests(TestCase):
    def setUp(self):
        create_facts()

    def permissive_editor_data(self, **extra):
        data = {
            "dimensions": ["region"],
            "source-TOTAL_FORMS": "1",
            "source-INITIAL_FORMS": "0",
            "source-MIN_NUM_FORMS": "0",
            "source-MAX_NUM_FORMS": "20",
            "slice-TOTAL_FORMS": "1",
            "slice-INITIAL_FORMS": "0",
            "slice-MIN_NUM_FORMS": "0",
            "slice-MAX_NUM_FORMS": "20",
        }
        data.update(extra)
        return data

    def test_empty_page_renders_django_controls_without_querying_or_javascript(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/reports/facts/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Report controls")
        self.assertContains(response, 'name="dimensions"')
        self.assertContains(response, 'name="ordering"')
        self.assertContains(response, "Run report")
        self.assertContains(response, "drf_cube/cube_explorer.css")
        self.assertNotContains(response, "cube_explorer.js")
        self.assertNotContains(response, "<tbody>")
        self.assertFalse(
            any(
                "testapp_fact" in query["sql"].lower()
                for query in queries.captured_queries
            )
        )

    def test_run_redirects_to_a_canonical_api_query_then_renders_the_table(self):
        response = self.client.get(
            "/reports/facts/",
            {"dimensions": ["region"], "editor_action": "run"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/reports/facts/?fields=region")

        report = self.client.get(response["Location"])

        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "Report output")
        self.assertContains(report, "Row amount sum")
        self.assertContains(report, "north")
        self.assertNotContains(report, "cube_explorer.js")

    def test_add_filter_is_a_server_render_without_an_api_query(self):
        data = self.permissive_editor_data(
            **{
                "source-0-lookup": "category__code__icontains",
                "source-0-value": "food",
                "editor_action": "add-source",
            }
        )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/reports/permissive-facts/", data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="source-1-lookup"')
        self.assertContains(response, 'value="category__code__icontains"')
        self.assertFalse(
            any(
                "testapp_fact" in query["sql"].lower()
                for query in queries.captured_queries
            )
        )

    def test_update_removes_selected_formset_rows_on_the_server(self):
        data = self.permissive_editor_data(
            **{
                "source-TOTAL_FORMS": "2",
                "source-0-lookup": "category__code__icontains",
                "source-0-value": "food",
                "source-0-DELETE": "on",
                "source-1-lookup": "category__code__icontains",
                "source-1-value": "household",
                "editor_action": "update",
            }
        )

        response = self.client.get("/reports/permissive-facts/", data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="household"')
        self.assertNotContains(response, 'value="food"')

    def test_canonical_permissive_report_executes_the_existing_api(self):
        response = self.client.get(
            "/reports/permissive-facts/?fields=region&category__code__icontains=food"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Report output")
        self.assertContains(response, "north")
        self.assertContains(response, "10.0")

    def test_api_validation_error_is_rendered_in_the_server_response(self):
        response = self.client.get(
            "/reports/facts/?fields=region&min_amount_sum_row=not-a-number"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The API could not process this report.")
        self.assertContains(response, "Enter a finite number")

    def test_pagination_links_are_rendered_server_side(self):
        response = self.client.get("/reports/facts/?fields=region&page_size=1")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Next")
        self.assertContains(response, "page=2")

    def test_an_authenticated_non_staff_user_can_run_a_report(self):
        user = get_user_model().objects.create_user(
            username="analyst", password="secret"
        )
        self.client.force_login(user)

        response = self.client.get("/reports/facts/?fields=region")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "north")

    def test_page_access_does_not_bypass_a_protected_api(self):
        response = self.client.get("/reports/protected-facts/?fields=region")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are not authorized to view these results.")
        self.assertNotContains(response, "<tbody>")
