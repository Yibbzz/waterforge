"""Test settings shared by every test."""

import logging
import os

import pyogrio
import pytest
from hypothesis import settings

# Test GeoPackages are throwaway: don't wait for each write to reach the disk (20x faster).
pyogrio.set_gdal_config_options({"OGR_SQLITE_SYNCHRONOUS": "OFF"})

settings.register_profile("default", max_examples=10, deadline=None)
settings.register_profile("ci", max_examples=50, deadline=None)
settings.register_profile("thorough", max_examples=500, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture(autouse=True)
def _fresh_logging():
    """The CLI points logging at the stderr pytest captured; drop that after each test, or
    later tests log to a closed stream ("I/O operation on closed file" in CI logs)."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if type(handler) is logging.StreamHandler:
            root.removeHandler(handler)
