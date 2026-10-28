# Real networks

Whole-city checks on open data, run nightly in CI and by hand with:

    WATERFORGE_DATA=<folder with townsville.gpkg and wellington.gpkg> uv run pytest -m realdata

The data is never committed (Wellington's terms: credit Wellington Water; not for commercial
use or detailed design). The download scripts are in
[open-water-checks](https://github.com/Yibbzz/open-water-checks) (`scripts/download_townsville.py`,
`scripts/download_wellington.py`); they write the GeoPackages with the layer names the
mappings in `examples/` use.

The baseline counts are the ones open-water-checks measured in October 2026 (its
`docs/findings.md`).
