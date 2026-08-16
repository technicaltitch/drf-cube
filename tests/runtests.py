"""Run the package's Django test suite without a host project."""

import os
import sys

import django
from django.conf import settings
from django.test.utils import get_runner


def main() -> None:
    """Configure the test settings and return Django's test-runner status."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
    django.setup()
    TestRunner = get_runner(settings)
    test_labels = sys.argv[1:] or ["tests"]
    failures = TestRunner(verbosity=2, interactive=False).run_tests(test_labels)
    sys.exit(bool(failures))


if __name__ == "__main__":
    main()
