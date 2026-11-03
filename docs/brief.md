# Utility Network Rules Engine: Drinking Water Brief

Oct 16, 2026 · @Nathan · updated Oct 27, 2026 after testing on two real networks (open-water-checks, an evaluation not published)

## Purpose and long-term goal

Build an open-source rules engine that does for utility network data what Esri's Utility Network does for network integrity. It knows which assets may connect, checks a whole network or a proposed change against those rules, and reports what breaks them. Digitizing, storage, versioning and analysis stay in other tools.

- **Long-term goal:** replace Esri's network capabilities with open tools, one capability at a time. Each is a tested engine with a CLI, with QGIS as one front end.
- **This project:** the network-integrity part of Esri's Utility Network, starting with drinking water.
- **EPANET's role:** the referee that proves the rules, as Valhalla proves NetworkForge's output. Because the engine builds an EPANET model to do that, it can also hand the model to modellers, with reasons where the data isn't ready. Testing showed nothing open does that today (see "What testing showed").
- **Pattern:** the same as NetworkForge, which builds routable street networks and proves its output by routing on it in Valhalla. Here the tool that proves the output is EPANET, the industry-standard open solver for drinking water networks.
- **Related work:** the Esri Network Analyst replacement (the routing.earth tutorial challenge, NetworkForge, easy-R5). This engine is a separate repo, tracked on the same multi-repo GitHub Project board.
- **Working name:** to be decided (see Open decisions). This brief calls it "the engine".

## The gap this fills

Open tools already cover storage, editing and field capture. What they lack is a reusable layer that knows a network's rules and enforces them, independent of any one schema or editor.

| Esri Utility Network capability | Open equivalent today | Status |
| --- | --- | --- |
| Storage, multi-user editing, field apps | PostGIS, GeoPackage, QGIS, QField | Covered |
| Attribute rules (automatic IDs, blocking invalid values) | QGIS default-value expressions and field constraints, Postgres constraints and triggers | Mostly covered |
| Versioning for long design jobs | Kart (Git-style versioning for geodata) | Partly covered |
| Industry data models (water, gas, electric) | Fixed schemas for water and wastewater (Giswater, TEKSI); conceptual standards (OGC MUDDI, IEC CIM, PODS Lite) | Partly covered |
| Connectivity rules (which asset may connect to which) | Built into Giswater's own water and drainage schema; nothing reusable | **Gap** |
| Associations (a vault contains valves) | QGIS table relations, with no rule semantics | **Gap** |
| Rule-aware editing (snap only where allowed, split a pipe when a valve lands on it) | QGIS snapping and topological editing, unaware of network rules | **Gap** |
| Network validation (invalid connections, dangles, what changed since the last check) | QGIS 3.40+ geometry checks and the Topology Checker: geometry only, no water knowledge, no proposal mode | **Gap** |
| Asset data to a hydraulic model, with reasons when it fails | Gusnet, Giswater, QGISRed and Network Studio each need the data moved into their own layers or schema first; EPANET's own messages name model nodes, not GIS features | **Gap** |

This engine targets the gap rows. It is the part that knows what is allowed; QGIS stays the editor and Postgres or GeoPackage the storage.

**How this maps to Esri.** Esri's Utility Network checks the rules: connectivity rules, validating edits, and error features to fix. Esri does not build or run hydraulic models; utilities on Esri buy partner add-ons that run inside ArcGIS Pro, such as Autodesk InfoWater Pro (which uses EPANET) and Bentley WaterGEMS. (Esri's "hydrology" tools are for surface water over terrain, not pipes.) This engine replaces the first, and its EPANET export gives open tools the bridge the second provides. A network the Utility Network calls valid can still fail in EPANET: Esri counts a pipe ending on another pipe's vertex as connected, EPANET doesn't, so the conversion step is needed either way.

**Prior art to study before building:**

- [Gusnet](https://github.com/angusmcb/gusnet) (formerly wntr-qgis, GPL-2.0, well tested, active): builds, checks and runs EPANET models in QGIS through WNTR. A modelling tool working on its own layers (junction points, valves as lines); asset data must be redrawn into them first. It snaps pipe ends to the nearest junction within 10% of the pipe's length, which quietly joins near misses. The closest neighbour: the engine could write Gusnet's layers as one output.
- [QGIS 3.40+ geometry checks](https://docs.qgis.org/latest/en/docs/user_manual/processing_algs/qgis/checkgeometry.html): generic geometry rules, runnable headless through `qgis_process`. Fast on a whole city, but noisy without water knowledge, and they count a pipe ending on another pipe's side as connected. The dangle and point-covered-by-line checks have a distance bug (reported to QGIS in October 2026); don't use them as a reference until it is fixed.
- [Giswater](https://github.com/giswater/plugin): open QGIS and PostgreSQL system for water supply and drainage. It enforces arc-node topology rules with database triggers and exports EPANET and SWMM input files. Closest working reference for water rules, but tied to its own schema.
- [QGISRed](https://qgisred.upv.es/): QGIS plugin built on the EPANET data model, with isolation-valve handling. Freeware, not open source; its core library is a separate install.
- [WNTR](https://github.com/USEPA/WNTR): the EPA's Python package for EPANET models. Useful for later analysis (valve segments), but version 1.5.0 bundles EPANET 2.0 and 2.2 only.
- [TEKSI](https://github.com/QWAT): Swiss-standard data models for drinking water and wastewater in QGIS and PostGIS.
- [Network Studio](https://plugins.qgis.org/plugins/network_studio/): QGIS plugin first published 7 October 2026 that copies the Esri Utility Network model across nine network types ([repo](https://github.com/belalmahmoud2018/network-studio-qgis)). Evaluated in October 2026: its engine treats a pipe end touching any point asset, or another pipe's vertex, as connected, so it passes most unsplit tees and misses most near misses; about 22,000 lines added in five uploads on one day, no tests, GUI only. Not a base to build on.

## What testing showed

Before building, the idea was tested on two whole cities' open data with today's tools (open-water-checks, not published; its findings are summarised here): Townsville, Australia (30,553 mains, Esri-style layers) and Wellington City, New Zealand (33,688 mains, exported from InfoNet with recorded node ids).

- **The gap is real.** No open tool takes asset data as it is (valves and sources as points, sizes as text, no junction layer) to an EPANET model, and none links EPANET's failures to GIS causes. Nothing labels a proposal's problems as new or pre-existing.
- **It is feasible.** About ten small rules took both cities from raw GIS data to a model EPANET 2.3.5 accepts: join pipe ends within 1 mm, read fittings as evidence, split a main where another pipe ends on its side, cut ring mains in two, join sources within 25 m, and fill missing sizes. With a token demand at every junction both models solve. The same code ran on both utilities; the only per-utility code was a field mapping of about 30 lines.
- **The biggest single cause** of EPANET's "cannot solve" (error 110) was a pipe ending on another main's side without the main being split there: 62–70% of the pieces no source reached. QGIS counts that as connected and EPANET can't name it.
- **Fittings are strong evidence.** End caps, hydrants and structures explain 57–67% of the pipe ends that meet nothing; the rest point at the errors (unsplit tees, near misses) and the ends that need a person.
- **Recorded node ids agree with geometry** almost everywhere (65,917 of 65,921 pipe ends within 1 mm of their node). Their value is as a cross-check: places where the two disagree, pipes with no recorded node, and in-use pipes joined to removed assets.
- **EPANET's messages need translating.** They name model nodes (`J13122`) that only exist because a conversion made them; the unconnected-node list stops at 10.

## First project: drinking water

Version 1 checks drinking-water supply networks against the rules EPANET and sound GIS practice require, and turns them into EPANET models. It reads a network, reports what breaks the rules, says which GIS problem causes each EPANET problem, and compares a proposed change against the existing network.

**In scope**

- Read an existing network from any GDAL source: GeoPackage, PostGIS, Shapefile, File Geodatabase feature classes. Pipes are lines; junctions, fittings, valves, hydrants, tanks, reservoirs and pumps are points.
- Map a utility's own fields and values to asset types through a config file, so any schema works unchanged. This includes fittings (end cap, tee, cross), valve status (normally open or closed), and sizes written as text (`"1000+"`).
- Work out connectivity from geometry (pipe ends joined by distance within a tolerance), or from explicit node-ID fields where a utility has them, and check that the two agree where both exist.
- Run the rule catalogue and report every violation with rule ID, severity, feature IDs and location.
- Use fittings as evidence: an end cap explains a pipe end that meets nothing; a tee or cross where pipes don't join is a likely error.
- `check` a whole network on its own (an existing network, or a standalone design such as a new development), and `check --proposed` a change (additions, modifications, removals) against it, failing only on violations the change introduces.
- Build the EPANET model internally through documented conversion steps (see "From GIS to EPANET"), and optionally write it as an `.inp` file, so EPANET itself can confirm the result.
- Explain EPANET's answers in GIS terms: every model node and link carries its source feature IDs, and every piece no source reaches is traced to the GIS issue at its edge.
- Machine-readable output for front ends and CI: JSON lines, a report file, an issues layer for QGIS, stable exit codes.
- Rule packs stored as data: sensible defaults shipped, overridable per utility.

**Out of scope for this project**

- Digitizing or any editing interface. A QGIS plugin comes later, in its own repo, calling the CLI.
- Storage, writing back to the source, versioning, multi-user editing.
- Tracing and analysis: upstream, downstream, isolation, shortest path, hydraulic design, calibration.
- Fixing the source data. The engine reports; people fix in their editor. Decided 27 October 2026: the engine may change anything in its own working copy (the conversion steps do), as long as the source is never modified, and each change is reported.
- Live checks on every edit inside QGIS, and Postgres triggers. Design the API so these are possible later.
- Associations (containment, attachment) and terminals. Water version 1 does not need them.
- Wastewater, stormwater, electric, gas and telecom networks.
- Esri-specific formats such as asset packages or Utility Network topology tables. Plain feature classes read through GDAL are fine.

## Where the rules come from

Every rule has one of two sources, recorded with the rule: what EPANET requires, or a GIS rule the engine owns and documents.

**1. EPANET's requirements: the source of truth.** EPANET is the industry-standard open solver for drinking water networks, maintained by the [Open Water Analytics community](https://github.com/OpenWaterAnalytics/EPANET) with the US EPA. Version 2.3 came out in July 2025; the latest release is [2.3.5](https://github.com/OpenWaterAnalytics/EPANET/releases), February 2026, and the engine uses it (see "EPANET version"). If EPANET refuses a model or can't solve it, the network breaks a rule. Within what it models, EPANET is strict ([error list](https://epanet22.readthedocs.io/en/latest/back_matter.html), [fuller list](https://tbn2net.com/help/SOLIDOSen/EpanetErrors.html)):

- a valve connected directly to a tank or reservoir, or to another valve in a disallowed arrangement
- a node not connected to any link
- a link whose start and end node are the same
- a reference to a node that doesn't exist, or an illegal numeric value
- a network without at least one tank or reservoir and one junction

**2. GIS rules the engine owns.** EPANET never sees the map; it only knows which nodes each link joins. Turning geometry into a network is the engine's job, so those rules are documented, tested and configurable: what counts as connected, near-misses, duplicates, point assets on pipes, node types and required attributes.

**Two check families, both run by default.** Analysis checks (the WE rules) answer "will EPANET, and later tracing, work on this network?" GIS checks (the WG rules) answer "does this feature make sense as drawn?" `--checks all` is the default; `--checks analysis` or `--checks gis` runs one family. One GIS problem often causes an analysis problem: a pipe ending on a main that was never split there is also, to EPANET, a piece of network no source reaches. The report links the two and names the GIS issue as the cause, so the fix lands in the right place.

**Two levels of EPANET check**

- **Loads:** EPANET accepts the converted model with no input errors, and the engine's own graph check finds every junction connected to a tank or reservoir. Needs topology and basic attributes only. The graph check is what explains EPANET's error 110 ("cannot solve"): in testing, that error came from pieces no source reached.
- **Solves:** a steady-state run completes and every node is fed from a source. Needs elevations, demands and source heads that GIS data often lacks, so it is optional and reports which inputs are missing instead of failing. With no demands at all EPANET reports "system unbalanced", so a token demand is not enough to claim a real solve.

**Water conventions that differ from NetworkForge's streets**

- Pipes that cross on the map do not connect unless a node sits at the crossing. NetworkForge joins crossing streets unless one is a bridge, tunnel or on another layer; water is the opposite. A tee or cross fitting recorded at a crossing is the exception worth flagging.
- Valves and pumps are usually points in GIS but links in EPANET. The engine turns each into a short link on the pipe it sits on, as Giswater does ([EPA export methods](https://github.com/giswater/plugin/wiki/epa_export_methods)). A valve point must sit on a pipe vertex or split point.
- A pipe or junction ending on another pipe's side, where that pipe was never split, is not connected for EPANET, though Esri and QGIS treat it as connected. EPANET can't say which pipe it was meant to join ([example](https://communities.bentley.com/products/hydraulics___hydrology/f/haestad-hydraulics-and-hydrology-forum/181425/how-can-i-downgrade-a-wtg-file/529147)), so the engine must. A setting chooses whether the export splits the pipe there or only flags it.
- Isolation (shut-off) valves are not part of EPANET's hydraulic model. In version 1 they are checked for placement only, as GIS rules. Normally closed valves (zone boundaries) do matter: the export cuts the pipe there.
- Reservoirs and tanks are often drawn as points beside the pipes, not on them.

## From GIS to EPANET: conversion steps

What testing showed a modeller has to do by hand today. Each step works on the engine's copy only, can be switched off in the rule pack, and reports every feature it touched as an Info issue (so the modeller sees what was assumed).

| Step | What it does | Default | Seen in testing |
| --- | --- | --- | --- |
| Join ends | Pipe ends within the tolerance become one junction, by distance (never by rounding to a grid: ends 0.1 mm apart either side of a grid line would stay apart) | 1 mm | Both cities |
| Split at side joins | Where another pipe's end lies on a pipe's side, at a vertex or between vertices, split the pipe there | On (setting: split or flag) | 110 mains (Townsville), 328 (Wellington) |
| Cut rings | A pipe that starts and ends at the same point becomes two pipes | On | 20 ring mains (Wellington) |
| Join sources | A tank or reservoir point joins the nearest pipe end within a reach distance | 25 m | 143 of 147, 72 of 77 |
| Read sizes | Sizes written as text become numbers; a size that can't be read is a WG-09 issue | On | `"1000+"` × 63 |
| Fill missing sizes | A pipe with no size gets a default, reported | Off for `check`, on for `export` | 140 and 479 mains |
| Close valves | A normally closed valve cuts the pipe it sits on | On | 220 and 827 valves (not yet tested in a model) |
| Keep IDs | Every model node and link records its source feature IDs (and a part number after a split, like NetworkForge's `nf_part`) | Always | |

## Existing data is messy: the baseline policy

A proposal is judged only on what it changes. Real utility networks always carry old errors, and a new extension must not fail because of a dangle someone drew years ago.

1. Check the existing network alone. Its violations are the baseline.
2. Merge the proposal into a copy of the existing network: additions, modifications and removals, with existing pipes split into tees where a new pipe meets them. Check the merged network.
3. Match violations between the two runs by fingerprint and label each one:
   - **New:** introduced by the proposal. These fail the check.
   - **Pre-existing:** in both runs. Reported, never fail the proposal.
   - **Resolved:** in the baseline but gone after the change. Reported.
4. Set the exit code from new violations only by default (`--fail-on new`). `--fail-on all` gives a full audit.

**Fingerprint:** rule ID, the sorted IDs of the features involved, and the location rounded to the tolerance. A pipe split by a proposal keeps its source ID plus a part number, as NetworkForge does with `nf_part`, so its old violations still match.

**Knock-on effects are caught** because the whole merged network is checked, not just the new features. A new pipe that leaves two valves illegally side by side, or a removal that cuts part of the network off from every source, shows up as new.

The existing network is never modified. A saved baseline (`--baseline-out`, reused with `--baseline`) avoids re-checking a large network for every proposal.

Testing confirmed the need: a proposed extension with a near miss and an unsplit tee added two reports to QGIS's 2,129, with nothing to say which were new, and the unsplit tee itself wasn't reported.

## Engine design: the NetworkForge pattern

[NetworkForge](https://github.com/Yibbzz/networkforge) (version 1.3.0, GPL-3.0) is the template. Copy its structure and habits; don't depend on it, since the domain is different. What it has, checked in its repo on 16 October 2026, and how each part carries over:

| Part | In NetworkForge | In this engine |
| --- | --- | --- |
| Packaging | Python 3.12+, `uv`, hatchling, `src/` layout, console script | Same |
| Engine boundary | No QGIS code in the repo; a separate plugin calls the CLI or API | Same |
| Commands | `build`, `check`, `presets`, `info` | `check`, `export`, `rules`, `info` |
| Machine output | `--json`: one JSON object per line on stdout (progress, warning, done, error); logs on stderr; `--id-field` names features | Same, plus an `issue` event per violation |
| Exit codes | 0 success, 1 other, 2 usage, 3 input, 4 download, 5 integrity | 0, 1, 2, 3 as NetworkForge; 5 = violations that fail; 4 unused |
| Errors | `NetworkForgeError` tree; plain message, `guide` anchor into the docs, per-feature `issues` | Same shape |
| Rules as data | `presets.py`, with a docs table that a test keeps in sync | Rule packs and a rule catalogue doc, kept in sync by a test |
| Oracle | Routes on the output PBF with Valhalla, version pinned | Loads and solves the output `.inp` with EPANET 2.3.5, version pinned |
| Tests | Unit; integration on a hand-built grid; Hypothesis property tests; live data; QGIS via `qgis_process`; Valhalla | Unit; integration on hand-built networks; Hypothesis; EPANET oracle; real-network regression; QGIS check later |
| CI | ruff, offline tests with an 88% coverage gate, Valhalla job, QGIS job, Windows job, nightly live and thorough Hypothesis runs | Same shape |
| Docs | README with "What it doesn't do"; `docs/network-rules.md` for users; `docs/network-analyst.md` maps each Esri feature to its tag and test | README with non-goals; `docs/rules.md`; `docs/esri-utility-network.md` mapping |
| Agent guide | `CLAUDE.md`: module map, dated decisions, gotchas | Same, from day one |
| Code habits | `logging` only, never `print`; vectorised shapely, numpy and pandas, no per-row loops | Same |
| Releases | Tag `vX.Y.Z`; a workflow checks, tests and publishes | Same |

**Proposed module map** (`src/<package>/`)

- `cli.py`: `check`, `export`, `rules`, `info`; `--json` events; exit codes.
- `inputs.py`: read layers through GDAL (pyogrio), check CRS and geometry types.
- `config.py`: load and validate the field mapping and rule pack.
- `connectivity.py`: geometry to nodes and links. Joins by distance within the tolerance, at pipe ends; pipe ends on another pipe's side; point assets placed on pipes; recorded node ids compared with geometry.
- `model.py`: the network as nodes, links and asset types, independent of storage, with source feature IDs on every element.
- `convert.py`: the conversion steps, each reporting what it touched.
- `rules/`: one module per family: `epanet.py` (structural), `gis.py`, `attributes.py`.
- `oracle.py`: write `.inp`, run load and solve through the pinned EPANET toolkit, translate its messages back to features.
- `compare.py`: merge a proposal, run the baseline diff, fingerprint violations.
- `report.py`: JSON events, report file, issues GeoPackage.
- `errors.py`: exception tree with `guide` anchors and per-feature `issues`.

Likely dependencies: geopandas, shapely 2, pyogrio, pyproj, networkx, scipy, and `owa-epanet` 2.3.5 (the official OWA Python wrapper, pinned). The engine writes the `.inp` text itself; WNTR is not needed for version 1.

**EPANET version.** Decided 27 October 2026: use the latest EPANET, 2.3.5, through `owa-epanet==2.3.5`, and upgrade deliberately. Tested on both cities' models: it gives the same answers as 2.2, lists every "same start and end node" error (2.2 stopped at 10), and reports each unconnected node as error 234 under an overall error 233 (still capped at 10). WNTR 1.5.0 can't be used for this: it bundles EPANET 2.0 and 2.2 only.

## Data contract and CLI

The engine reads two kinds of input, both read-only: the network, and optionally a proposed change in the same shape.

**Inputs**

- **Network layers:** a pipes layer (lines) plus point layers for junctions, fittings, valves, hydrants, tanks, reservoirs and pumps, or one points layer with a type field. Any GDAL source; PostGIS by connection string.
- **Field mapping** (a config file): which layer, field and value means which asset type, and which fields hold ID, diameter, material, status, elevation, demand, and recorded start and end node IDs where a utility has them. Only assets in use are read (the mapping says how "in use" is recorded).
- **CRS:** a projected CRS in metres. Geographic or Web Mercator input is reprojected with a warning, as NetworkForge does with UTM.
- **Tolerance:** one snapping distance in metres for joins. Default 1 mm (tested on both cities). Near misses use a separate, larger distance (default 1 m).
- **Proposed change:** the same schema. A feature whose ID matches an existing feature modifies it; `remove=yes` removes it; features with new IDs are additions and must not reuse an existing ID. This mirrors NetworkForge's way-ID edits and `remove=yes`.
- **Standalone designs:** a new network not yet joined to supply has no tank or reservoir, so WE-06 and WE-08 would always fail. Its connection points can be marked as supply points in the mapping, standing in for the existing network.

**Commands**

```bash
# Check a network on its own: an existing network or a standalone design, both families
<engine> check --network existing.gpkg --mapping utility.yaml --json

# One family only: GIS representation, or analysis readiness (default: --checks all)
<engine> check --network existing.gpkg --mapping utility.yaml --checks gis
<engine> check --network existing.gpkg --mapping utility.yaml --checks analysis

# A proposed change against the existing network: only new violations fail
<engine> check --network existing.gpkg --proposed proposed.gpkg --mapping utility.yaml \
    --fail-on new --report report.json --issues issues.gpkg

# Straight from PostGIS
<engine> check --network "PG:dbname=water host=db" --mapping utility.yaml

# EPANET input, for the oracle or for modellers
<engine> export --network existing.gpkg --mapping utility.yaml --inp network.inp

<engine> rules --json   # every rule: ID, family, source, severity, description, doc anchor
<engine> info --json    # versions, asset types, allowed connections, limits
```

**Outputs**

- `--json`: one object per line on stdout. Events: `progress`, `warning`, `issue`, `done`, `error`.
- An `issue` carries the rule ID, its family (analysis or GIS), the GIS issue that caused it if there is one, severity, status (new, pre-existing, resolved), feature IDs, location, message and a `guide` anchor into `docs/rules.md`.
- `--report`: the same issues as one JSON file. `--issues`: a GeoPackage layer of issue points and lines, so a QGIS user can select the features involved.
- `export` writes the `.inp` and, alongside it, a table from every model node and link to its source feature IDs. Writing Gusnet's layers is a candidate later output.
- Exit codes: 0 nothing failing; 1 unexpected error; 2 bad usage; 3 unusable input; 5 violations that fail under `--fail-on`.
- Once released, flags, JSON shapes and exit codes stay stable. A breaking change means a new major version.

## Initial rule catalogue

Version 1 starts with two families: nine EPANET-derived rules (WE, the analysis family) and sixteen GIS rules (WG, the GIS family). Severities are defaults that a utility's rule pack can change; Error fails a check, Warning and Info are reported only. EPANET error numbers are from the [EPANET 2.2 manual](https://epanet22.readthedocs.io/en/latest/back_matter.html), a [fuller error list](https://tbn2net.com/help/SOLIDOSen/EpanetErrors.html), and EPANET 2.3.5's own messages.

| ID | Rule | Source | Level | Severity |
| --- | --- | --- | --- | --- |
| WE-01 | Every link connects two nodes that exist | EPANET error 203 | Loads | Error |
| WE-02 | A link's start and end node differ | EPANET error 222 | Loads | Error |
| WE-03 | Every node connects to at least one link | EPANET errors 233 and 234 (2.3) | Loads | Error |
| WE-04 | No valve connects directly to a tank or reservoir | EPANET error 219 | Loads | Error |
| WE-05 | No disallowed valve arrangement: PRVs sharing a downstream node or in series, PSVs sharing an upstream node or in series, a PSV straight after a PRV | EPANET error 220 | Loads | Error |
| WE-06 | At least one tank or reservoir and one junction | EPANET input requirement | Loads | Error |
| WE-07 | Numeric properties are legal: diameter, length and roughness positive | EPANET errors 202, 211 | Loads | Error |
| WE-08 | Every junction is fed from a tank or reservoir | EPANET hydraulic run (error 110, disconnected nodes) | Loads (engine graph check), confirmed at Solves | Error |
| WE-09 | A steady-state run completes | EPANET hydraulic run | Solves | Warning |
| WG-01 | A pipe end meets a node or another pipe end within tolerance, unless it ends at an allowed end asset such as an end cap | GIS rule | Map | Error |
| WG-02 | Near miss: a pipe end close to another pipe or node but not joined (undershoot or overshoot) | GIS rule | Map | Warning |
| WG-03 | A point asset on a pipe's interior sits on a vertex or split point, so it is actually connected | GIS rule | Map | Error |
| WG-04 | Pipes crossing with no node at the crossing are listed for review, not joined; with a tee or cross fitting recorded there, it is a likely missed join | GIS rule | Map | Info (Warning with a fitting) |
| WG-05 | No duplicate or overlapping pipes | GIS rule | Map | Error |
| WG-06 | Geometry is valid, single-part and longer than zero | GIS rule | Map | Error |
| WG-07 | A node's number of connections suits its type: an end cap has one, a tee three | GIS rule | Map | Warning |
| WG-08 | Each asset has the attributes its type requires, such as a pipe's diameter and material | GIS rule | Map | Error |
| WG-09 | Asset types and values are known to the mapping, such as a diameter that reads as a number within range | GIS rule | Map | Error |
| WG-10 | Each valve or pump point converts to a valid EPANET link: one pipe in, one pipe out | GIS rule | Map | Error |
| WG-11 | Pipe diameter changes only at a fitting node (candidate for after v0.1) | GIS rule | Map | Warning |
| WG-12 | A pipe end on another pipe's side, where that pipe isn't split (split by the export, or flagged, per setting) | GIS rule; cause of WE-08 | Map | Error |
| WG-13 | A ring: a pipe that starts and ends at the same point (cut in two by the export) | GIS rule; cause of WE-02 | Map | Warning |
| WG-14 | Where a utility records node IDs, they agree with geometry: a pipe end lies at its recorded node, and pipes that meet share one | GIS rule | Map | Warning |
| WG-15 | An in-use pipe names a node that is removed, abandoned or missing, or names none | GIS rule | Map | Warning |
| WG-16 | A tank or reservoir is joined to the network, or within the reach distance of a pipe end | GIS rule; cause of WE-08 | Map | Error |

Every rule ships with a passing and a failing fixture and an entry in `docs/rules.md`. A rule with no recorded source does not ship.

## Testing: EPANET as the oracle

NetworkForge proves its output by routing on it in Valhalla. This engine proves its rules by agreeing with EPANET, version pinned and upgraded deliberately.

- **Agreement tests:** for each EPANET-derived rule, a fixture that breaks it. The engine must flag it, and EPANET must reject the exported `.inp` with the matching error. A clean fixture must pass both. Any disagreement is a bug, or a documented difference with a reason, like NetworkForge's `DIFFERENCES` list for Valhalla.
- **Round trips:** EPANET's classic example networks (Net1 to Net3) turned into GIS layers, checked, exported back to `.inp` and run. Every node, link and connection of the original must survive the trip.
- **Unit tests:** each GIS rule with a passing and a failing fixture.
- **Integration tests:** small hand-built networks, like NetworkForge's street grid, including networks seeded with old errors for the compare mode.
- **Cause links and families:** for each known pairing, such as WG-12 (a pipe ending on a main that was never split) causing WE-08 (a piece no source reaches), a fixture asserts the report links the analysis issue to its GIS cause. `--checks gis` and `--checks analysis` each run correctly on their own.
- **Property tests (Hypothesis):** random proposals on generated networks. A pre-existing violation is never labelled new; adding a correctly joined pipe introduces no new error; applying then removing a proposal returns the baseline.
- **Mutation tests:** inject one known error of each kind into a clean network; exactly that rule must fire, labelled new.
- **Real networks (nightly):** Townsville and Wellington, downloaded from their open data portals (respecting each licence; data never committed). The counts from open-water-checks are the starting regression baseline, and "Loads" must pass after the conversion steps.
- **Distances:** computed relative to the segment, never with a formula that multiplies raw coordinates (the bug found in QGIS's checks). A test with real-world projected coordinates and short segments guards this.
- **QGIS check (later):** the issues layer opens and selects features through `qgis_process` in the official QGIS Docker image, as NetworkForge's QGIS job does.
- **People:** a water modeller reviews the rules and categories on real data before v0.1.

CI runs ruff, the offline suite with a coverage gate, and the EPANET oracle on every push and pull request, plus a nightly thorough Hypothesis run and the real-network run.

## Potential timeline

&#91;embedded content: water engine milestones · 6 stages and a release\]

Dates assume a start on 19 October 2026, agent-assisted work and a break over the new year. Shift them as work lands; the order matters more than the dates.

1. **Setup and evaluation:** evaluation done 26–27 October 2026 (open-water-checks: QGIS checks, Gusnet, Network Studio, Giswater and QGISRed, on two real networks). Still to do: repo, `uv`, CI, `CLAUDE.md`, README with non-goals; settle the remaining open decisions; show the findings to a few hydraulic modellers.
2. **Inputs and connectivity:** read layers, apply the field mapping, turn geometry into a network, place point assets on pipes, compare with recorded node IDs.
3. **EPANET export and rules:** conversion steps, `.inp` writer with feature IDs, oracle harness on EPANET 2.3.5, rules WE-01 to WE-08 with agreement tests, round trips of EPANET's example networks.
4. **GIS rules and outputs:** rules WG-01 to WG-16, JSON events, the two check families with cause links, report file, issues GeoPackage, `rules` and `info` commands.
5. **Proposed vs existing:** merging, tees, removals, fingerprints, the baseline diff and its property tests.
6. **Solve level, docs, trial:** rule WE-09 and the Solves-level confirmation, `docs/rules.md`, the Esri mapping doc, the real-network runs and a modeller's review.
7. **v0.1 release:** tag and publish once "Done for water" is ticked.

## Done for water, and what comes after

Water is finished when every box below is ticked. No other domain starts before then.

- [ ] Every EPANET-derived rule has an agreement test passing against the pinned EPANET version
- [x] Every GIS rule is documented in `docs/rules.md` with a passing and a failing fixture
- [ ] `check` and `check --proposed` work on GeoPackage and PostGIS inputs, with JSON shapes and exit codes documented and frozen
- [x] Property tests show a pre-existing violation never fails a proposal
- [x] EPANET's example networks round-trip with every node, link and connection intact
- [ ] Townsville and Wellington reach "Loads" after the conversion steps, with every remaining unfed piece traced to a GIS cause, and the findings written up
- [ ] A water modeller has reviewed the rules on real data
- [x] `docs/esri-utility-network.md` maps each relevant Esri capability to a rule and its test, and lists what is not covered
- [x] The offline suite passes in CI with a coverage gate
- [ ] v0.1 is tagged and released

**Later phases, one at a time**

| Phase | Domain | Source-of-truth tool | New concepts |
| --- | --- | --- | --- |
| 2 | Wastewater and stormwater | EPA SWMM | Gravity flow, inverts, outfalls, flow direction |
| 3 | Electric distribution | OpenDSS or pandapower, chosen then | Terminals (transformer sides), phases, switches, voltage tiers |
| 4 | Gas | pandapipes | Pressure tiers, regulator stations |

Front ends follow the engine: a thin QGIS plugin in its own repo after water v0.1, then live per-edit checks and Postgres triggers built on the same API.

## Open decisions

These are the owner's calls. Agents record each decision with its date in `CLAUDE.md` once made.

| Decision | Options | Notes |
| --- | --- | --- |
| ~~Name and package name~~ | **Decided 28 Oct 2026:** WaterForge (`waterforge`) | No Esri names or logos in it |
| ~~License~~ | **Decided 28 Oct 2026:** GPL-3.0, like NetworkForge | GPL fits the QGIS ecosystem; permissive makes reuse by other tools easier |
| ~~EPANET access~~ | **Decided 27 Oct 2026:** EPANET 2.3.5 through `owa-epanet`, pinned | WNTR bundles 2.2 at most; tested on both cities |
| ~~Default snapping tolerance~~ | **Suggested by testing:** 1 mm for joins, 1 m for near misses | Configurable; confirm with a modeller |
| ~~Pipe ends on another pipe's side~~ | **Decided 28 Oct 2026:** split in the model by default (`split_side_joins`), still reported as WG-12; confirm with a modeller | Esri and QGIS treat them as connected; EPANET doesn't |
| ~~Config format~~ | **Decided 28 Oct 2026:** YAML | Field mapping and rule packs use the same format |
| ~~Valves and pumps as points~~ | **Decided 28 Oct 2026:** both: points become links in the model, lines are used as drawn | Most utility GIS draws them as points; both test cities do |
| ~~Missing elevations and demands at the Solves level~~ | **Decided 30 Oct to 3 Nov 2026:** elevations from a DEM (`--dem`), else the nearest known; demands from customers layers, scaled to a published total (`total_demand`) | Loads level never needs them; neither test city publishes demands |
| ~~First real dataset~~ | **Done:** Townsville (CC BY 4.0) and Wellington (non-commercial) open data | A third, from a utility that joins at vertices Esri-style, would test the split setting |
| ~~Network Studio~~ | **Evaluated October 2026:** stay independent | Its connectivity model hides the errors this engine exists to find |
| Gusnet | Write Gusnet's layers as an output, or stay with `.inp` only | Gusnet could serve as a second, front-end check |

## Guardrails for agents

The engine checks and reports. Everything below protects that scope; a change to any of it needs the owner's decision, recorded in `CLAUDE.md`.

Keep a copy of this brief in the repo as `docs/brief.md`, and read it before starting any task.

1. **Stay in scope.** No digitizing, storage, writing back, versioning, tracing or analysis. The source data is never modified; anything may change in the engine's own working copy, and each change is reported.
2. **Water only** until every box in "Done for water" is ticked.
3. **Every rule has a source.** An ID, a source (an EPANET error or a written GIS rationale), a default severity, a docs entry, and a passing and a failing fixture. No source, no rule.
4. **EPANET is the referee.** When the engine and EPANET disagree, fix the engine or record the difference with its reason.
5. **Never fail a proposal for an old error.** Pre-existing violations are reported, not failed.
6. **Input is read-only.** The engine never modifies the network it reads.
7. **Stable interface.** Once released, CLI flags, JSON shapes and exit codes only change with a new major version.
8. **No QGIS code in this repo.** Front ends live in their own repos and call the CLI or API.
9. **NetworkForge habits:** `logging` only, never `print`; vectorised shapely, numpy and pandas; no per-row loops over large networks.
10. **Clean room.** Build from EPANET's documentation, open standards and public descriptions of concepts. Don't copy Esri documentation text, don't reverse engineer Esri software, and don't use Esri names or logos in the product. Calling it "an open alternative to Esri's Utility Network" is fine.
11. **Pin the oracle.** EPANET 2.3.5 through `owa-epanet==2.3.5`; an upgrade is a deliberate step with the agreement tests re-run.
12. **Keep `CLAUDE.md` current:** module map, dated decisions and gotchas, as NetworkForge does.
13. **Measure distances properly.** Relative to the segment, by distance not grid rounding; don't trust another tool's geometry check as ground truth without checking it.

## References

**Related projects**

- [NetworkForge](https://github.com/Yibbzz/networkforge): the pattern this engine follows
- open-water-checks: the evaluation behind this update (not published)
- [routing.earth tutorial challenge](https://github.com/routing-earth/qgis-network-analyst-tutorial-challenge): the Network Analyst replacement work
- [easy-R5](https://github.com/GISBoost/easy-R5): transit accessibility plugin, candidate for the same engine split

**EPANET, the source of truth**

- [OWA-EPANET on GitHub](https://github.com/OpenWaterAnalytics/EPANET) and its [releases](https://github.com/OpenWaterAnalytics/EPANET/releases) (2.3.5, February 2026)
- [owa-epanet on PyPI](https://pypi.org/project/owa-epanet/): the official Python wrapper
- [EPANET 2.2 manual: error messages](https://epanet22.readthedocs.io/en/latest/back_matter.html)
- [Fuller EPANET error list](https://tbn2net.com/help/SOLIDOSen/EpanetErrors.html)
- [EPANET toolkit 2.3 release notes (QGISRed)](https://qgisred.upv.es/?p=2350)
- [WNTR on GitHub](https://github.com/usepa/wntr) and its [valve layers and segmentation](https://deepwiki.com/USEPA/WNTR/7.2-valve-layers-and-segmentation)
- [Disconnected-node example from a model built in GIS](https://communities.bentley.com/products/hydraulics___hydrology/f/haestad-hydraulics-and-hydrology-forum/181425/how-can-i-downgrade-a-wtg-file/529147)

**Prior art in QGIS**

- [Gusnet](https://github.com/angusmcb/gusnet): EPANET modelling in QGIS through WNTR
- [QGIS geometry checks](https://docs.qgis.org/latest/en/docs/user_manual/processing_algs/qgis/checkgeometry.html) (3.40+)
- [Giswater plugin](https://github.com/giswater/plugin), its [topology control rules](https://github.com/giswater/plugin/wiki/state-topocontrol), [EPA export methods](https://github.com/giswater/plugin/wiki/epa_export_methods) and [mincut tool](https://docs.giswater.org/master/en/docs/giswater/for-users/dialogs/mincut.html)
- [QGISRed isolated-segment tool](https://qgisred.upv.es/?p=2339)
- [TEKSI drinking water module](https://github.com/QWAT)
- [Network Studio plugin](https://plugins.qgis.org/plugins/network_studio/) and its [repo](https://github.com/belalmahmoud2018/network-studio-qgis)

**Open data used in testing**

- [Townsville City Council open data](https://data-tsvcitycouncil.opendata.arcgis.com/) (CC BY 4.0)
- [Wellington Water open data](https://data-wellingtonwater.opendata.arcgis.com/) (credit Wellington Water; not for commercial use or detailed design)

**Standards and later phases**

- [OGC MUDDI approved as a standard](https://www.ogc.org/announcement/ogc-approves-model-for-underground-data-definition-and-integration-as-official-standard/)
- [pandapipes](https://pypi.org/project/pandapipes/0.5.0/), for the gas phase
