"""Regression coverage for the documented source-checkout demo."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .testapp.models import Fact


class DemoSeedCommandTests(TestCase):
    def test_seed_demo_replaces_data_with_the_shared_fact_set(self):
        first_output = StringIO()
        call_command("seed_demo", stdout=first_output)

        call_command("seed_demo", stdout=StringIO())

        self.assertEqual(Fact.objects.count(), 7)
        self.assertIn("Seeded 7 demo facts.", first_output.getvalue())
