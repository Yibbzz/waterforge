# WaterForge

Open rules engine for drinking water network GIS data: checks a network against network rules
and turns it into an EPANET model, with EPANET 2.3.5 as the referee (as Valhalla is for
NetworkForge). The plan, scope and guardrails are in `docs/brief.md`: **read it before any
task** and follow its "Guardrails for agents". Private repo: github.com/Yibbzz/waterforge.

Python >= 3.12, managed with `uv`. Run things with `uv run ...`. The package is installed
editable by `uv sync` (hatchling). No QGIS code in this repo.

Tests: `uv run pytest` (offline, ~1.5 min, includes the EPANET agreement tests and round trips;
`-m postgis` needs a database, see Gotchas).
`uv run pytest -m realdata` runs whole cities (needs `WATERFORGE_DATA`, see
tests/realdata/README.md; ~2 min). Lint: `uv run ruff check .`. Coverage: `uv run pytest --cov`.

## Working with the user

- The user (Nathan) is learning as they go: plain language, lead with the result, clear next
  steps, verify rather than assume.
- **Commit and push only when asked.** Commits show only the user:
  `git -c user.name="Nathan" -c user.email="nathanleeks@gmail.com" commit ...`, with **no**
  `Co-Authored-By` or "Generated with Claude" lines.
- Be honest about limits: what was run vs only read about; correct earlier claims when wrong.
- The evaluation behind this engine is in `~/open-water-checks` (its `docs/findings.md`);
  don't change it or `~/networkforge*` from here.

## Source (`src/waterforge/`)

- `cli.py` - `waterforge check|export|rules|info`. `--json` = JSON lines on stdout (progress,
  warning, issue, done, error); human logs on stderr. Exit codes 0 / 1 / 2 / 3 input or mapping /
  5 failing issues (export: model EPANET can't load); 4 unused. `check --proposed`,
  `--baseline[-out]`, `--fail-on new|all|none`, `--fail-level error|warning`, `--solve`.
  Keep flags and JSON shapes stable once released.
- `compare.py` - `check_proposal`: baseline check (or `load_baseline`), `merge` (ids replace,
  `remove=yes` removes, new ids add; pipes split where a proposed pipe meets an existing one's
  side), merged check, `label` (fingerprint match; WE-08 matched by its pipes).
- `config.py` - the YAML mapping + rule pack (`load_mapping`, `parse_mapping`): layers per asset
  kind (pipes, junctions, fittings, valves, hydrants, pumps, structures), number fields
  (`NUMBER_FIELDS`), valve types, `Settings`, `Steps`, `severity`.
  Collects every problem and raises `MappingError`. Values compared via `normalise_value`.
  YAML reads `off` as False: handled for severities.
- `inputs.py` - `read_network(source, mapping)`: GDAL via pyogrio, `where` per layer, CRS
  (geographic / 3857 -> UTM with a warning), explode multi-part (WG-06), diameters parsed
  from numbers or text (WG-08 / WG-09). Result: `Network.pipes`, `Network.points`
  (asset, layer, fid, asset_type, closed, number fields) and `Network.links` (valves / pumps
  drawn as lines). `proposal=True`: layers optional, `remove` column. Checks layers and
  fields first, all at once.
- `connectivity.py` - distance-based joins (`node_ids`: STRtree + connected components),
  `side_joins`, `nearest`. Never round to a grid; never multiply raw coordinates.
- `convert.py` - `build_model`: the conversion steps (short pipes out, split side joins, cut
  rings, valves and pumps as links, join ends with sources clustered in, sources on pipe ends
  = that node else joined within reach, fill sizes, junction elevations / demands), each
  reporting issues. Works on a copy; the source is never modified.
- `model.py` - `Model`: junctions, reservoirs (tanks and reservoirs, `kind`), links (pipe /
  valve / pump / connector; `LINK_COLUMNS`) with source `layer`, `fid`, `part`, `piece`.
  Names J/R/P/V/U/L + number (EPANET-safe).
- `rules/catalogue.py` - every rule (ID, family, source, level, severity, implemented).
  docs/rules.md must match (tests/unit/test_catalogue.py).
- `rules/gis.py` - WG rules needing the joined network: lonely ends (WG-01/02), crossings
  (04), duplicates (05), fittings (07), points on pipes (03), size changes (11), node ids
  (14/15).
- `rules/epanet.py` - WE rules on the model; `valve_rules` (WE-04/05, exactly EPANET's
  valvecheck); `unfed` (WE-08) with cause links to GIS issues.
- `oracle.py` - `write_inp` (junctions, reservoirs, tanks, pipes, valves, pumps),
  `run_epanet` (owa-epanet toolkit; reads EPANET's report; `pressures=True`), `referee`
  (token-demand copy), `solve` (as recorded). `CODE_RULE` maps EPANET codes to rules.
- `check.py` - `check_network`: read -> convert -> GIS rules -> WE rules -> referee ->
  `compare_with_epanet` (disagreements; `KNOWN_DIFFERENCES`) -> optional `solves_level`
  (WE-09) -> `finalise`. Second referee run with sizes filled when only sizes stopped EPANET.
- `issues.py` - `Issue` (rule, message, features, location, severity, cause, status, data);
  `id` = fingerprint (rule + sorted features + location rounded to the tolerance).
- `report.py` - report JSON, issues GeoPackage (issue_points / issue_lines), element table.
- `postgis.py` - PostGIS layers via psycopg (`list_layers`, `fields`, `read_layer`), read-only.
- `dem.py` - `sample(path, xs, ys, crs)`: terrain raster values (rasterio, extra `dem`).
- `errors.py` - `WaterForgeError` / `InputError` / `MappingError` / `EpanetError`.

## Decisions (dated)

- 2026-10-28: name **WaterForge** (package `waterforge`), **GPL-3.0** (as NetworkForge),
  **YAML** for mappings and rule packs, private GitHub repo under Yibbzz.
- 2026-10-27: EPANET **2.3.5** through `owa-epanet==2.3.5`, pinned; upgrades are deliberate
  with the agreement tests re-run. The engine writes the `.inp` itself; no WNTR.
- 2026-10-27: "no automatic fixing" = the source is never modified. The engine may change
  anything in its own working copy (split, cut rings, join sources, fill sizes, close valves)
  as long as each change is reported.
- 2026-10-28: side joins are split in the model by default (`split_side_joins: true`); the
  WG-12 issue stays an Error so the GIS gets fixed.
- 2026-10-28: closed valves cut the model by default (brief); Townsville unfed pieces go
  49 -> 122, Wellington 175 -> 265 with them. WG-10 severities: junction of 3+ pipes = error,
  end of one pipe = info, not near a mapped pipe = warning.
- 2026-10-28: a tank with elevation, levels and diameter mapped is an EPANET tank, else a
  reservoir (head = head, else elevation + init level, else 50 m). A source on a pipe end is
  that node (no connector).
- 2026-10-28: valves and pumps may be points or lines (both supported; brief open decision).
  Pumps use POWER (default 10 kW) until curves are mapped; a control valve with no setting is
  an open TCV (WG-08).
- 2026-10-28: WE-04 / WE-05 follow EPANET 2.3.5's valvecheck exactly (not the brief's summary:
  PSV-then-PRV is allowed; FCV pairs added; 219 only for PRV / PSV / FCV).
- 2026-10-28: WG-03 default Warning (brief said Error): measured, the engine places such
  assets anyway; tapping bands and end caps left out. Beside a pipe = Info.
- 2026-10-28: proposals: `--fail-on new|all|none` + `--fail-level error|warning` (the old
  `--fail-on error|warning|none` is gone). A proposed pipe meeting an existing pipe's side
  splits it (a designed connection, not WG-12). WE-08 matched by pipes.
- 2026-10-28: JSON output format 1 (docs/json.md, `cli.FORMAT`, tests/integration/
  test_json_contract.py). The EPANET summary uses `accepts_input` / `runs` (was
  `opened` / `loads`). Adding keys isn't breaking; anything else bumps `FORMAT`.
- 2026-10-28: pumps with `design_flow` + `design_head` get a one-point head curve (C<pump>);
  else POWER. `--dem` (extra `dem`, rasterio) fills missing elevations before the
  nearest-known fill.
- 2026-10-28: Wellington tanks: `twl_m` (top water level, m above datum, checked against the
  ground beside each tank) is the head; `elevation_m` is empty for tanks.

## Gotchas

- On a GeoDataFrame, `.type` is the geometry type and `.length` / `.head` are pandas: use
  `frame["length"]`, and the points' type column is `asset_type`.
- EPANET 2.3.5: input errors raise on `open` (details only in the report file); a piece with
  no source stops `solveH` with error 110, but a piece cut off only by **closed links** doesn't:
  EPANET warns "Node X disconnected" and finishes. Both are WE-08 (`EpanetResult.rules`).
- With no demand anywhere EPANET can't flag disconnected nodes: the referee writes a token
  demand (0.01 L/s) copy. EPANET writes some report lines twice; `run_epanet` drops repeats.
- EPANET stops at the first error 202 (illegal value), and lists at most 10 unconnected nodes;
  disconnected-node warnings stop after a few ("N additional nodes disconnected").
- EPANET 2.3.5 bug: the input reader's valvecheck skips the valve written just before (it
  passes `net->Nlinks`); fixed on dev (946bb73c, 2026-10-08), unreleased. Handled as
  `check.KNOWN_DIFFERENCES["WE-05"]`; `test_epanet_235_misses_valves_written_one_after_the_other`
  fails once a fixed EPANET is pinned: then drop the difference.
- pandas rows: `row.head` is the method, not the `head` column: use `row["head"]`.
- pyogrio's bundled GDAL (3.12.4) has no PostgreSQL driver: PostGIS is read with psycopg in
  `postgis.py` (extra `postgis`). `ogr2ogr` into PostgreSQL lowercases field names: fields
  match case-insensitively when unambiguous (`inputs._check_source`).
- A proposal ignores the mapping's `where` (its file needn't have the filter's fields).
- PostGIS tests: `docker run -d --rm -e POSTGRES_PASSWORD=wf -p 55432:5432
  postgis/postgis:16-3.4`, then `WATERFORGE_POSTGIS="PG:host=localhost port=55432
  dbname=postgres user=postgres password=wf" uv run pytest -m postgis`. Townsville read from
  PostGIS gives the same counts as from the GeoPackage (checked 2026-10-28).
- owa-epanet raises Python warnings for EPANET warning codes: suppressed in `run_epanet`.
- Fittings beside a junction (a few cm) cap branches not in the mapped pipes: WG-07 matches
  fittings within the tolerance only; WG-01 lets an end cap explain only its nearest pipe end.
- Test GeoPackage writes are slow unless `OGR_SQLITE_SYNCHRONOUS=OFF` (tests/conftest.py).
- Townsville reuses `ASSET_NUMBER` 0 for many mains: report per original row, not per id.

## Next steps (in order)

1. A modeller reviews the rules on real data (the modeller summary doc in open-water-checks);
   fold their answers in (defaults: 1 mm, 1 m, 25 m; split side joins; closed valves).
2. Demands from customer meters or billing; multi-point pump curves and patterns (the Solves
   level is a single steady state). Try `--dem` on Wellington with LINZ's 8 m DEM.
3. Release v0.1 once the brief's "Done for water" list is ticked (left: the modeller review
   and the tag: bump `version`, move CHANGELOG's Unreleased to [0.1.0], push `v0.1.0`;
   `.github/workflows/release.yml` builds and publishes the GitHub release).
4. Later: a `connections:` rule table (which asset types may connect), Gusnet layers as an
   output, the QGIS plugin in its own repo.
