"""Small relational data model that replaces HEA's report models in tests."""

from django.db import models


class Category(models.Model):
    code = models.CharField(max_length=20, unique=True)
    label = models.CharField(max_length=50)

    def __str__(self) -> str:
        return self.code


class Fact(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    region = models.CharField(max_length=20)
    amount = models.FloatField()
    quantity = models.FloatField()
