# WaterForge and Esri's Utility Network

What WaterForge covers of the network-integrity part of Esri's Utility Network for drinking
water, with the rule and the test behind each, and what it doesn't cover. Written from public
descriptions of the concepts (brief guardrail 10: no Esri text or software reverse-engineered).
"Utility Network" names Esri's product only to compare; WaterForge is an open alternative, not
a copy.

The two products work differently. The Utility Network keeps a topology inside the database
and checks edits as they are made, in areas marked as changed. WaterForge reads any layers as
they are (GeoPackage, PostGIS, FileGDB, Shapefile), works out the connectivity each time, and
checks the whole network or a proposed change. It also builds and runs the EPANET model, which
the Utility Network leaves to partner add-ons.

## Covered

| Capability | What it means for water | WaterForge | Tests |
| --- | --- | --- | --- |
| Connectivity from geometry | Which pipes and devices are joined | Pipe ends within the tolerance (1 mm) are one junction, by distance | `test_gis_rules.py` (real-coordinate tests), `test_properties.py` (position invariance) |
| Connection at a pipe's side (mid-span) | A pipe ending on another's side: the Utility Network can count it as connected; EPANET can't | WG-12, and the model splits the pipe there (`split_side_joins`) | `test_wg12_*` |
| Point devices on lines | A valve or fitting must sit on a vertex or pipe end to be connected | WG-03 | `test_wg03_*` |
| Connectivity rules: what may end where | A pipe may end at an end cap, a hydrant, a structure | WG-01 with `allowed_end_assets` | `test_wg01_*` |
| Connectivity rules: device connections | A fitting's number of connections suits its type; a valve has one pipe each side | WG-07, WG-10 | `test_wg07_*`, `test_wg10_*` |
| Connectivity rules: which asset types may connect | The utility's own table: no hydrant or service on a trunk main, raw water never joins treated mains, trunk to distribution only through a valve. Written as what must never meet (with exceptions), not every allowed pair | WG-18 with pipe `class:` and `connections:` | `test_wg18_*` |
| Error features: dangles | A pipe end that meets nothing | WG-01 (and WG-02 for near misses, which the Utility Network doesn't separate) | `test_wg01_*`, `test_wg02_*` |
| Error features: geometry | Empty, multi-part or zero-length lines; duplicate features | WG-06, WG-05 | `test_wg06_*`, `test_wg05_*` |
| Error features: crossings | Lines crossing with no junction | WG-04 (with fittings as evidence) | `test_wg04_*` |
| Attribute rules: required values and domains | A pipe must have a diameter; values must be valid | WG-08, WG-09 | `test_wg08_wg09_*`, `test_wg09_*` |
| Validating the network | A full check, with errors to fix | `waterforge check`, issues as JSON and a GeoPackage layer | `test_cli.py` |
| Validating an edit or a design | Only the errors a change introduces | `check --proposed`: new / pre-existing / resolved | `test_proposals.py`, proposal property tests |
| Connected to a source | Every part of the network is fed (in the Utility Network: subnetworks traced from controllers) | WE-08 (a graph check from tanks and reservoirs; closed valves cut it), confirmed by EPANET | `test_agreement.py`, `test_wg02_*`, `test_wg12_flag_only_*` |
| Normally closed devices | Zone boundaries | Closed valves cut the model (`close_valves`) | `test_wg10_*` |
| Recorded connectivity | Where connectivity is stored, it agrees with the drawing | WG-14, WG-15 (node ids, as InfoNet exports have) | `test_wg14_*` |

## Beyond the Utility Network

| Capability | WaterForge | Tests |
| --- | --- | --- |
| A hydraulic model from the asset data | `export`: EPANET .inp plus a table from each model element to its GIS feature | `test_cli.py`, `test_roundtrip.py` |
| The model proven by the solver | EPANET 2.3.5 is asked at every check; disagreements are reported | `test_agreement.py` |
| GIS causes of model problems | An unfed piece names the side join, near miss or missing source that cuts it off | `test_wg02_near_miss_is_reported_and_causes_the_unfed_piece` |
| A steady-state run | `check --solve` (WE-09) with the inputs that were missing | `test_model_assets.py` |

## Not covered

| Capability | Why | Where it could go |
| --- | --- | --- |
| Point-to-point connection rules (e.g. which valve types may sit next to each other) | WG-18 covers pipe classes with pipes and point assets; EPANET's own valve rules are WE-04 / WE-05 | Extend `connections:` |
| Associations: containment (a chamber holds valves), attachment, structural | Out of scope for water version 1 (brief) | A later version |
| Terminals (sides of a device) | Not needed for water; needed for electric | The electric phase |
| Subnetworks, tiers and tracing (upstream, isolation, valve isolation) | Analysis is out of scope (brief) | WNTR's valve segmentation could build on the model |
| Checking edits as they are made, in changed areas only | WaterForge checks whole networks or proposals; a saved baseline avoids re-checking | A QGIS plugin and Postgres triggers on the same API (brief: later) |
| Storing topology, versions and multi-user editing | Storage and editing stay in PostGIS, GeoPackage, QGIS and Kart | Not planned |
| Network diagrams | Not part of integrity | Not planned |
| Pump curves, patterns, controls, water quality | The model is for Loads and a steady state | Mapping fields for curves later |
