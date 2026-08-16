"""Development settings for the documented source-checkout demo."""

from pathlib import Path

from .settings import *  # noqa: F403


DEBUG = True
ALLOWED_HOSTS: list[str] = []
DATABASES["default"]["NAME"] = Path(__file__).resolve().parent.parent / ".demo.sqlite3"  # noqa: F405
INSTALLED_APPS = [*INSTALLED_APPS, "django.contrib.staticfiles"]  # noqa: F405
STATIC_URL = "static/"
