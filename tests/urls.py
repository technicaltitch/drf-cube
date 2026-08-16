"""Test-only router for the cube endpoint."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from drf_cube.explorer import cube_explorer_page

from .testapp.cube import (
    FactCubeViewSet,
    PermissiveFactCubeViewSet,
    ProtectedFactCubeViewSet,
)

router = SimpleRouter()
router.register("facts", FactCubeViewSet, basename="fact-cube")
router.register(
    "permissive-facts",
    PermissiveFactCubeViewSet,
    basename="permissive-fact-cube",
)
router.register(
    "protected-facts", ProtectedFactCubeViewSet, basename="protected-fact-cube"
)

urlpatterns = [
    path("", include(router.urls)),
    path(
        "reports/facts/",
        cube_explorer_page(
            api_url_name="fact-cube-list",
            cube_viewset_class=FactCubeViewSet,
            title="Facts cube",
            description="Explore fact totals.",
        ),
        name="facts-cube-explorer",
    ),
    path(
        "reports/permissive-facts/",
        cube_explorer_page(
            api_url_name="permissive-fact-cube-list",
            cube_viewset_class=PermissiveFactCubeViewSet,
            title="Permissive facts cube",
        ),
        name="permissive-facts-cube-explorer",
    ),
    path(
        "reports/protected-facts/",
        cube_explorer_page(
            api_url_name="protected-fact-cube-list",
            cube_viewset_class=ProtectedFactCubeViewSet,
            title="Protected facts cube",
        ),
        name="protected-facts-cube-explorer",
    ),
]
