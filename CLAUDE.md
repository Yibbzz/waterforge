# WaterForge

Open rules engine for drinking water network GIS data: checks a network against network rules
and turns it into an EPANET model, with EPANET 2.3.5 as the referee (as Valhalla is for
NetworkForge). The plan, scope and guardrails are in `docs/brief.md`: **read it before any
task** and follow its "Guardrails for agents". Private repo: github.com/Yibbzz/waterforge.

Python >= 3.12, managed with `uv`. Run things with `uv run ...`. The package is installed
editable by `uv sync` (hatchling). No QGIS code in this repo.

Tests: `uv run pytest` (offline, ~1 min, includes the EPANET agreement tests).
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
  5 failing issues (export: model EPANET can't load); 4 unused. Keep flags and JSON shapes
  stable once released.
- `config.py` - the YAML mapping + rule pack (`load_mapping`, `parse_mapping`): layers per asset
  kind (pipes, fittings, valves, hydrants, structures), `Settings`, `Steps`, `severity`.
  Collects every problem and raises `MappingError`. Values compared via `normalise_value`.
  YAML reads `off` as False: handled for severities.
- `inputs.py` - `read_network(source, mapping)`: GDAL via pyogrio, `where` per layer, CRS
  (geographic / 3857 -> UTM with a warning), explode multi-part (WG-06), diameters parsed
  from numbers or text (WG-08 / WG-09). Result: `Network.pipes` + `Network.points`
  (asset, layer, fid, asset_type, closed). Checks layers and fields first, all at once.
- `connectivity.py` - distance-based joins (`node_ids`: STRtree + connected components),
  `side_joins`, `nearest`. Never round to a grid; never multiply raw coordinates.
- `convert.py` - `build_model`: the conversion steps (short pipes out, split side joins, cut
  rings, closed valves, join ends, join sources, fill sizes), each reporting issues. Works on
  a copy; the source is never modified.
- `model.py` - `Model`: junctions, reservoirs, links (pipe / valve / connector) with source
  `layer`, `fid`, `part`, `piece`. Names J/R/P/V/L + number (EPANET-safe).
- `rules/catalogue.py` - every rule (ID, family, source, level, severity, implemented).
  docs/rules.md must match (tests/unit/test_catalogue.py).
- `rules/gis.py` - WG rules needing the joined network: lonely ends (WG-01/02), crossings
  (04), duplicates (05), fittings (07), node ids (14/15).
- `rules/epanet.py` - WE rules on the model; `unfed` (WE-08) with cause links to GIS issues.
- `oracle.py` - `write_inp`, `run_epanet` (owa-epanet toolkit; reads EPANET's report),
  `referee` (token-demand copy). `CODE_RULE` maps EPANET codes to rules.
- `check.py` - `check_network`: read -> convert -> GIS rules -> WE rules -> referee ->
  `compare_with_epanet` (disagreements) -> `finalise`. Second referee run with sizes filled
  when only sizes stopped EPANET.
- `issues.py` - `Issue` (rule, message, features, location, severity, cause, status, data);
  `id` = fingerprint (rule + sorted features + location rounded to the tolerance).
- `report.py` - report JSON, issues GeoPackage (issue_points / issue_lines), element table.
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
- 2026-10-28: tanks are reservoirs at the Loads level (head 50 m); elevations and demands 0.

## Gotchas

- On a GeoDataFrame, `.type` is the geometry type and `.length` / `.head` are pandas: use
  `frame["length"]`, and the points' type column is `asset_type`.
- EPANET 2.3.5: input errors raise on `open` (details only in the report file); a piece with
  no source stops `solveH` with error 110, but a piece cut off only by **closed links** doesn't:
  EPANET warns "Node X disconnected" and finishes. Both are WE-08 (`EpanetResult.rules`).
- With no demand anywhere EPANET can't flag disconnected nodes: the referee writes a token
  demand (0.01 L/s) copy. EPANET writes some report lines twice; `run_epanet` drops repeats.
- EPANET stops at the first error 202 (illegal value), and lists at most 10 unconnected nodes.
- owa-epanet raises Python warnings for EPANET warning codes: suppressed in `run_epanet`.
- Fittings beside a junction (a few cm) cap branches not in the mapped pipes: WG-07 matches
  fittings within the tolerance only; WG-01 lets an end cap explain only its nearest pipe end.
- Test GeoPackage writes are slow unless `OGR_SQLITE_SYNCHRONOUS=OFF` (tests/conftest.py).
- Townsville reuses `ASSET_NUMBER` 0 for many mains: report per original row, not per id.

## Next steps (in order)

1. Proposed vs existing (`check --proposed`, stage 5 of the brief): merge, fingerprints,
   new / pre-existing / resolved, `--fail-on new`, property tests.
2. Round trips of EPANET's example networks (Net1-Net3) through GIS layers.
3. Solves level (WE-09): elevations from fittings / a DEM, demands, real tank levels; control
   valves and pumps as links (WE-04, WE-05, WG-10 for open control valves).
4. WG-03 and WG-11; `docs/esri-utility-network.md` mapping each Esri capability to a rule.
5. A modeller reviews the rules on real data (the modeller summary doc in open-water-checks).
