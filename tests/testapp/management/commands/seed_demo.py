"""Seed the source-checkout demo with the aggregation test data."""

from django.core.management.base import BaseCommand

from tests.factories import create_facts
from tests.testapp.models import Category, Fact


class Command(BaseCommand):
    """Replace the demo's facts with the shared deterministic data set."""

    help = "Replace demo facts with the data used by the aggregation tests."

    def handle(self, *args, **options) -> None:
        Fact.objects.all().delete()
        Category.objects.all().delete()
        create_facts()
        self.stdout.write(self.style.SUCCESS("Seeded 7 demo facts."))
