from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from .testapp.cube import FactCubeSerializer


class AggregatingSerializerTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def serializer_for(self, path="/"):
        request = Request(self.factory.get(path))
        return FactCubeSerializer(context={"request": request})

    def test_defaults_to_all_public_fields_in_declared_order(self):
        serializer = self.serializer_for()

        self.assertEqual(
            list(serializer.get_fields()),
            ["region", "category_code", "amount", "quantity"],
        )

    def test_requested_fields_keep_request_order_and_ignore_unknown_fields(self):
        serializer = self.serializer_for(
            "/?fields=category_code,region,not_a_cube_dimension"
        )

        self.assertEqual(list(serializer.get_fields()), ["category_code", "region"])

    def test_ordering_dimensions_are_normalized_before_grouping(self):
        serializer = self.serializer_for(
            "/?fields=amount&ordering=-region,category__code,amount_sum_row"
        )

        self.assertEqual(
            list(serializer.get_fields()), ["amount", "region", "category_code"]
        )

    def test_representation_uses_public_names_and_generated_field_order(self):
        serializer = self.serializer_for("/?fields=category_code,region")

        representation = serializer.to_representation(
            {
                "category__code": "food",
                "region": "north",
                "amount_sum_row": 10.0,
                "quantity_avg_row": 1.0,
            }
        )

        self.assertEqual(
            list(representation),
            [
                "category_code",
                "region",
                "amount_sum_row",
                "quantity_avg_row",
            ],
        )
        self.assertEqual(representation["category_code"], "food")
