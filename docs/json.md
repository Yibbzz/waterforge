# JSON output (format 1)

What `--json`, `--report` and `--issues` produce, for programs that drive WaterForge (a QGIS
plugin, CI). Once released, this is a stable interface: a breaking change (a key removed or
renamed, a type changed, a meaning changed) raises `format` and the major version. Adding a key
is not breaking, so readers should ignore keys they don't know.
`tests/integration/test_json_contract.py` checks every event against this page.

With `--json`, stdout carries one JSON object per line; stderr carries human-readable logs.

## Events

Every event has `"event"`. Types: `str`, `int`, `float` (or int), `bool`, `list`, `dict`,
`null`; "or" means either.

### progress

| Key | Type | |
| --- | --- | --- |
| step | int | 1-based |
| total | int | |
| message | str | |

### warning

| Key | Type | |
| --- | --- | --- |
| message | str | e.g. ids used more than once, a CRS reprojected, EPANET disagreeing |

### issue

One per rule violation.

| Key | Type | |
| --- | --- | --- |
| id | str | 12 hex characters: a fingerprint of the rule, the features and the location; the same problem has the same id in two runs |
| rule | str | `WE-01` ... `WG-17` ([rules.md](rules.md)) |
| family | str | `analysis` or `gis` |
| severity | str | `error`, `warning` or `info` |
| message | str | plain language |
| features | list | of `{"layer": str, "id": str or int}`: the mapping's layer and id |
| location | list or dict or null | `[x, y]` (a point, in the network's CRS); `{"centre": [x, y], "bounds": [x0, y0, x1, y1]}` (lines, e.g. an unfed piece); `null` (the whole network) |
| guide | str | `rules.md#<rule>` |
| cause | list | optional: ids of the GIS issues that cause this one |
| status | str | optional, with `--proposed`: `new`, `pre-existing` or `resolved` |
| data | dict | optional: rule-specific details (distances, counts, EPANET elements) |

### done (check)

| Key | Type | |
| --- | --- | --- |
| format | int | 1 |
| failing | int | issues that fail under `--fail-on` and `--fail-level` |
| summary | dict | see below |
| outputs | dict | `report`, `issues`, `baseline`: paths written |

### done (export)

| Key | Type | |
| --- | --- | --- |
| format | int | 1 |
| loads | bool | EPANET accepts the written .inp |
| summary | dict | see below |
| outputs | dict | `inp`, `table`, and `report`, `issues` if asked |

### summary

| Key | Type | |
| --- | --- | --- |
| pipes | int | pipes read (multi-part lines count once per part) |
| point_assets | int | |
| model | dict | `junctions`, `reservoirs`, `tanks`, `pipes`, `closed_valves`, `control_valves`, `pumps` (ints) |
| changes | dict | what the conversion steps did, by name (ints); names may be added |
| issues | dict | issues per rule id (ints) |
| severity | dict | `error`, `warning`, `info` (ints) |
| epanet | dict or null | the referee's answer (below); null with `--no-epanet` or on export |
| solve | dict or null | with `--solve`: the steady-state run (below, plus `pressures`, `warnings`) |
| disagreements | list | of str: where the engine and EPANET differ |
| proposal | dict | only with `--proposed`: `pipes_added`, `pipes_modified`, `pipes_removed`, the same for `points_` and `links_`, and `status` (`new`, `pre-existing`, `resolved` counts) |

An EPANET answer: `version` (str, "2.3.5"), `accepts_input` (bool), `runs` (bool), `errors`
(dict, EPANET error code -> count), `messages` (list of str, at most 20), and optionally
`with_sizes_filled` (another answer, when only missing sizes stopped EPANET), `pressures`
(`junctions`, `min_m`, `max_m`, `negative`) and `warnings` (list of str).

### error

| Key | Type | |
| --- | --- | --- |
| type | str | `InputError`, `MappingError`, `EpanetError`, or a Python exception name |
| message | str | |
| guide | str | optional: `<file>.md#anchor` |
| issues | list | optional: `{"feature": null or id, "message": str}`, e.g. every mapping problem |

### rule (`waterforge rules --json`)

`id`, `family`, `title`, `source`, `level`, `severity` (str), `implemented` (bool), `guide` (str).

### info (`waterforge info --json`)

`format` (int), `waterforge`, `epanet` (str), `asset_kinds`, `fitting_types`,
`structure_types` (list of str), `rules`, `implemented` (int), `exit_codes` (dict).

## Report file (`--report`)

One JSON object: `format`, `waterforge`, `epanet`, `command`, `network`, `mapping` (str or int
as in `info`), `summary` (as above) and `issues` (a list of issue events without the `event`
key).

## Issues layer (`--issues`)

A GeoPackage with two layers in the network's CRS: `issue_points` (points) and `issue_lines`
(multi-lines, e.g. unfed pieces). Fields: `id`, `rule`, `family`, `severity`, `status`,
`message`, `features` (`layer/id` joined by `; `, at most 200), `cause` (ids joined by `,`).
Issues about the whole network (no location) aren't in the layer.

## Exit codes

0 nothing failing; 1 unexpected error; 2 bad usage; 3 unusable input or mapping; 5 issues that
fail (export: EPANET can't load the model). 4 is unused.
