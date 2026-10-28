"""Test settings shared by every test."""

import os

import pyogrio
from hypothesis import settings

# Test GeoPackages are throwaway: don't wait for each write to reach the disk (20x faster).
pyogrio.set_gdal_config_options({"OGR_SQLITE_SYNCHRONOUS": "OFF"})

settings.register_profile("default", max_examples=10, deadline=None)
settings.register_profile("ci", max_examples=50, deadline=None)
settings.register_profile("thorough", max_examples=500, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
