"""Deterministic fact data shared by the aggregation tests."""

from .testapp.models import Category, Fact


def create_facts() -> None:
    """Create three regions with enough variation for cube semantics."""
    food = Category.objects.create(code="food", label="Food")
    household = Category.objects.create(code="household", label="Household")
    transport = Category.objects.create(code="transport", label="Transport")

    Fact.objects.bulk_create(
        [
            Fact(category=food, region="north", amount=10, quantity=1),
            Fact(category=household, region="north", amount=20, quantity=2),
            Fact(category=transport, region="north", amount=5, quantity=3),
            Fact(category=food, region="south", amount=30, quantity=3),
            Fact(category=household, region="south", amount=40, quantity=4),
            Fact(category=transport, region="south", amount=5, quantity=2),
            Fact(category=food, region="zero", amount=0, quantity=0),
        ]
    )
