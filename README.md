# WaterForge

Check a drinking water network's GIS data against network rules, and turn it into an
[EPANET](https://github.com/OpenWaterAnalytics/EPANET) model, with EPANET itself as the
referee. An open alternative to the network-integrity part of Esri's Utility Network, plus
the GIS-to-model bridge that Esri users buy as add-ons.

It reads a utility's layers as they are: valves and sources as points, sizes as text, no
junction layer. A YAML [field mapping](docs/mapping.md) says what each layer and field means.
WaterForge then:

- reports what breaks the [rules](docs/rules.md), with rule ID, severity, the features
  involved and a location;
- says which GIS problem causes each EPANET problem. For example, a pipe that ends on another
  main's side without that main being split leaves part of the network unfed in EPANET;
- builds the EPANET model through documented conversion steps, reporting every change it
  makes to its own copy (the source is never modified);
- asks EPANET 2.3.5 whether it agrees, and reports any disagreement;
- checks a proposed change against the existing network and fails only on the problems the
  change introduces, never on old errors.

> **Status: pre-alpha (0.1.0.dev0).** All 25 rules of the brief are implemented. Checking whole
> networks and proposals, exporting `.inp` and the steady-state run are tested on two cities'
> open data (Townsville, Australia and Wellington, New Zealand) and on EPANET's example
> networks. A water modeller's review is still to come. Flags and JSON shapes may still change
> before 0.1.0.

## Install

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Yibbzz/waterforge && cd waterforge
uv sync
uv run waterforge --version
```

## Use

```bash
# Check a network: issues on screen, exit code 5 if any are errors
uv run waterforge check --network network.gpkg --mapping utility.yaml

# For programs and CI: one JSON object per line; plus a report and an issues layer for QGIS
uv run waterforge check --network network.gpkg --mapping utility.yaml --json \
    --report report.json --issues issues.gpkg

# One family only: GIS rules (WG) or analysis rules (WE)
uv run waterforge check --network network.gpkg --mapping utility.yaml --checks gis

# A proposed change: only the problems it introduces fail (old errors are pre-existing)
uv run waterforge check --network network.gpkg --proposed design.gpkg --mapping utility.yaml

# Also run the model once with the recorded elevations, demands and source heads
uv run waterforge check --network network.gpkg --mapping utility.yaml --solve

# An EPANET model, plus a table from each model element to its GIS feature
uv run waterforge export --network network.gpkg --mapping utility.yaml --inp network.inp

uv run waterforge rules     # every rule: ID, severity, level
uv run waterforge info      # versions, asset types, exit codes
```

`--network` takes a GeoPackage, Shapefile folder or File Geodatabase (anything GDAL reads), or
PostGIS (`"PG:host=db dbname=water user=gis"`; install with `uv sync --extra postgis` or
`pip install 'waterforge[postgis]'`). `--dem terrain.tif` fills missing junction elevations
from a terrain raster, or from a folder of tiles such as LINZ's (`waterforge[dem]`). Example mappings: [`examples/`](examples/).
The JSON output is documented, field by field, in [docs/json.md](docs/json.md).

Exit codes: 0 nothing failing, 1 unexpected error, 2 bad usage, 3 unusable input or mapping,
5 issues that fail under `--fail-on` (`export`: a model EPANET can't load).

## What it found on real data

On Townsville's 30,553 mains and Wellington's 33,688 (each checked in about 15 seconds):

| | Townsville | Wellington |
| --- | --- | --- |
| Pipe ends on another main's side, not split (WG-12) | 152 | 379 |
| Near misses within 1 m (WG-02) | 118 | 341 |
| Tanks and reservoirs joined / in the data (WG-16) | 143 / 147 | 72 / 77 |
| Pieces no source reaches, closed valves left open (WE-08) | 49 | 175 |
| ... with closed valves cutting the network (the default) | 122 | 265 |
| Valves and tees on a pipe between its vertices (WG-03) | 145 | 236 |
| Size changes with no fitting recorded (WG-11) | 239 | 326 |
| EPANET 2.3.5 | loads the export | loads the export |

The counts with closed valves left open match the hand-made evaluation in
[open-water-checks](https://github.com/Yibbzz/open-water-checks) exactly; a nightly test
keeps them that way. EPANET's example networks Net1 to Net3 survive a round trip through GIS
layers with every node, link and connection intact.

How it compares with Esri's Utility Network, capability by capability:
[docs/esri-utility-network.md](docs/esri-utility-network.md).

## What it doesn't do

- **Edit or fix your data.** It reports; people fix in their editor. It changes only its own
  working copy, to build the model, and reports each change.
- Store, version or write back data; trace, isolate or analyse networks; calibrate models.
- QGIS code: a plugin will live in its own repo and call this CLI.
- Wastewater, stormwater, electric or gas networks (water first).
- Join near misses or guess connections: a person decides.

## Develop

```bash
uv run pytest              # offline suite, including the EPANET agreement tests (~1 min)
uv run ruff check .
uv run pytest --cov        # coverage (CI gate: 85%)
uv run python -m tests.realdata.download data && WATERFORGE_DATA=data uv run pytest -m realdata
```

The plan, scope and guardrails are in [docs/brief.md](docs/brief.md); the agent guide is
[CLAUDE.md](CLAUDE.md).

## License

GPL-3.0, like NetworkForge and QGIS. EPANET is public domain (US EPA); the Open Water
Analytics toolkit and `owa-epanet` are MIT.
