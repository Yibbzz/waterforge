# Real networks

Whole-city checks on open data, run nightly in CI and by hand with:

    WATERFORGE_DATA=<folder with townsville.gpkg and wellington.gpkg> uv run pytest -m realdata

The data is never committed (Wellington's terms: credit Wellington Water; not for commercial
use or detailed design). Download it with

    uv run python -m tests.realdata.download data

which writes `townsville.gpkg` and `wellington.gpkg` with the layer names the mappings in
`examples/` use, and LINZ's Wellington City 1 m DEM (2025, CC BY 4.0, ~245 MB) into
`dem/wellington-city_2025/` for the `--dem` test (skipped when it is missing).

The baseline counts are the ones open-water-checks measured in October 2026 (its
`docs/findings.md`).
