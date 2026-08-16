#!/usr/bin/env python3
"""Run the source-checkout demo project."""

import os
import sys


def main() -> None:
    """Run Django commands with the demo settings."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.demo_settings")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
