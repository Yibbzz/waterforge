# Changelog

All notable changes to WaterForge. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). From 0.1.0 the command line (flags, exit codes)
and the JSON output ([docs/json.md](docs/json.md), format 1) only change in a breaking way
with a new major version (before 1.0: a new minor version, said here).

## [Unreleased]

First version, built from the brief (docs/brief.md) and the open-water-checks evaluation.

### Added
- `waterforge check`: a drinking water network's GIS layers, read through a YAML field
  mapping from GeoPackage, Shapefile, File Geodatabase or PostGIS, checked against 26 rules:
  9 from EPANET's requirements (WE) and 17 GIS rules (WG). Issues name their rule, features,
  location and the GIS issues that cause them. JSON events, a report file and an issues
  GeoPackage for QGIS.
- The conversion to an EPANET model in documented steps on the engine's own copy (the
  source is never modified): side joins split, rings cut, valves and pumps as links (points
  or lines), sources joined, sizes filled, elevations and demands from point assets or a DEM
  (`--dem`: one raster or a folder of tiles; only the cells under the junctions are read).
- `customers:` layers (meters, properties, buildings or service connections; any geometry)
  add demand at the nearest junction: mapped, else `customer_demand` (0.007 L/s). A layer
  may be mapped twice (Wellington: service connections from the pipe layer).
- `check --solve`: each area below zero pressure is its own WE-09 issue, naming the nearest pump
  station within 1 km (where pumps are probably missing from the model).
- A source with no head or levels takes the DEM's ground plus `source_above_ground` (5 m).
- WG-17 (with a DEM): recorded elevations more than `dem_tolerance` (5 m) from the terrain,
  grouped into batches that are off by the same amount.
- EPANET 2.3.5 (owa-epanet, pinned) as the referee at every check; disagreements reported,
  one known EPANET 2.3.5 gap documented (WE-05).
- `check --proposed`: a change against the existing network, issues labelled new,
  pre-existing or resolved; `--fail-on new|all|none`, `--fail-level`, saved baselines.
- `check --solve`: the steady-state run (WE-09) with the inputs that were missing; pieces no
  source reaches are left out of the run (and said), so a whole city still solves.
- `waterforge export`: the .inp and a table from every model element to its feature.
- `waterforge rules`, `waterforge info`.
- Tests: every rule with a passing and a failing fixture, EPANET agreement tests, EPANET's
  Net1-Net3 round trips, property tests, mutation tests for proposals, whole-city regression
  on Townsville and Wellington open data, PostGIS.
