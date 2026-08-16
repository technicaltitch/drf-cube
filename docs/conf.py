"""Sphinx configuration for the drf-cube documentation."""

from importlib.metadata import version as distribution_version

project = "drf-cube"
author = "Chris Preager"
release = distribution_version(project)
version = release

extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
exclude_patterns = ["_build"]

html_theme = "alabaster"
