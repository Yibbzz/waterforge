# The field mapping

A mapping is a YAML file that tells WaterForge which layers, fields and values in a utility's
own data mean which asset types. Any schema works unchanged: nothing has to be moved into a
special layout first. The same file can hold a [rule pack](#rule-packs).

Working examples: [`examples/townsville.yaml`](../examples/townsville.yaml) (Esri-style layers
with numeric type codes) and [`examples/wellington.yaml`](../examples/wellington.yaml) (an
InfoNet export with recorded node ids).

## Layers

Seven asset kinds, each a list of layers (a utility with two valve layers lists both):

```yaml
pipes:                       # lines; at least one pipe layer is required
  - layer: mains             # the layer's name in the --network source
    where: "NOTINUSE = 'N' OR NOTINUSE IS NULL"   # assets in use only (GDAL SQL)
    id: ASSET_NUMBER         # names the feature in every issue
    diameter: PIPE_SIZE      # a number, or text such as "150" or "1000+"
    material: PIPE_MAT       # optional
    start_node: start_node_id  # optional: recorded node at each end (WG-14, WG-15)
    end_node: end_node_id

fittings:                    # points
  - layer: fittings
    id: ASSET_NUMBER
    type:
      field: USER_TYPE
      values: {3103: end cap, 3101: tee, 3102: cross}
      default: other         # values not listed; leave out to report them (WG-09)

valves:                      # points, or lines drawn as links
  - layer: system_valves
    id: ASSET_NUMBER
    closed: {field: NORMALPOSITION, values: [0]}   # which values mean normally closed
    type:                    # isolation (the default), prv, psv, pbv, fcv, tcv
      field: VALVE_TYPE
      values: {PRV: prv, SV: isolation}
      default: isolation
    setting: SETTING         # control valves: pressure (m) or flow (L/s), as EPANET

hydrants:
  - layer: hydrants
    id: ASSET_NUMBER
    elevation: GROUND_RL     # any point layer may give an elevation (the Solves level)

junctions:                   # optional points at pipe ends: elevations and demands
  - layer: nodes
    id: NODE_ID
    elevation: ELEVATION
    demand: BASE_DEMAND      # L/s

customers:                   # optional: where water is used (points, lines or areas)
  - layer: meters            # meters, properties, buildings or service connections
    id: METER_ID
    demand: AVG_USE_LPS      # L/s; customers with none get settings.customer_demand
                             # each adds its demand to the nearest junction (customer_reach);
                             # a line (service connection) counts at its end nearest a pipe,
                             # an area at a point inside it. A layer may be mapped twice, e.g.
                             # the pipe layer as pipes and, filtered by `where`, as customers.

pumps:                       # points, or lines drawn as links
  - layer: pumps
    id: PUMP_ID
    design_flow: DUTY_FLOW   # L/s and m at the duty point: a one-point pump curve
    design_head: DUTY_HEAD
    power: POWER_KW          # kW: used when there is no duty point

structures:                  # tanks, reservoirs, pump stations, ...
  - layer: structures
    id: ASSET_NUMBER
    type:
      field: DESCRIPT
      values: {RESERVOIR: reservoir, WATER_TANK: tank, BOOSTER_PUMP: pump station}
      default: other
    head: TWL                # a reservoir's head (m above datum)
    elevation: FLOOR_RL      # a tank: elevation, init_level, min_level, max_level and
    init_level: INIT         # tank_diameter make it an EPANET tank; with fewer it is a
    min_level: BWL           # reservoir (head = head, else elevation + init_level)
    max_level: TWL_DEPTH
    tank_diameter: DIAMETER
  - layer: pump_stations
    type: pump station       # one type for the whole layer
```

**Number fields** (`elevation`, `demand`, `roughness`, `setting`, `power`, `head`, the levels
and `tank_diameter`) are read as numbers; a value that isn't one is reported (WG-09) and
treated as missing. Pipes may also map `roughness` (Hazen-Williams C; 130 when missing).

**Valves and pumps as lines.** A utility or a model may draw valves and pumps as short lines
between nodes instead of points. Either works: a line's ends join the pipe ends and sources
they touch (within the tolerance). Open isolation valves aren't in the model either way.

- `layer` is required; everything else is optional. A layer can be given by name alone:
  `hydrants: [hydrants]`.
- `where` is passed to GDAL (or to PostgreSQL for PostGIS), so it works the same on
  GeoPackage, PostGIS and FileGDB. Use it to keep only assets in use. Removed and abandoned
  assets left in the layers would otherwise be checked as if they were in the ground. A
  proposal doesn't apply it: everything proposed is meant to be in use.
- Field names match regardless of capitals when only one field does (loading data into
  PostgreSQL usually lowercases them: `ASSET_NUMBER` becomes `asset_number`).
- **PostGIS:** `--network "PG:host=db port=5432 dbname=water user=gis"` (a libpq connection
  string; the password can come from `~/.pgpass` or `PGPASSWORD`). Layers are tables or views
  with a geometry column, named `table` or `schema.table`. Needs `waterforge[postgis]`
  (psycopg): the GDAL inside pyogrio's packages has no PostgreSQL driver.
- `id`: without it, features are named by row number. Ids should be unique within a layer; the
  engine warns when they aren't.
- Values are compared as text, ignoring case and surrounding spaces, and `3103.0` matches
  `3103`.
- **Fitting types:** end cap, tee, cross, bend, reducer, joint, tapping band, other.
  **Structure types:** reservoir, tank, pump station, other. Tanks and reservoirs are the
  sources that feed the network.
- Every problem in the mapping is listed at once (exit code 3), including fields a layer
  doesn't have, with a suggestion when only the capitals differ.

## Proposals

A proposed change (`check --proposed`) uses the same mapping. Its file may hold only some of
the layers. A feature whose id matches an existing feature in the same layer replaces it; a
field named `remove` (any capitals; the name is `remove_field` in the settings) set to yes,
true or 1 removes it; new ids are added. See [rules.md](rules.md#proposed-changes).

## CRS

Distances such as the 1 mm tolerance need a projected CRS in metres. Pipes in a geographic CRS
(degrees) or Web Mercator are reprojected to their UTM zone, with a warning. A layer with no
CRS can be given one: `crs: EPSG:7855` at the top of the mapping. Point layers are reprojected
to the pipes' CRS.

## Rule packs

Settings, conversion steps and severities, in the mapping or in a separate file given with
`--rules` (which overrides the mapping):

```yaml
settings:
  tolerance: 0.001         # m: pipe ends this close are one junction
  near_miss: 1.0           # m: a loose end this close to another pipe is a near miss (WG-02)
  at_asset: 0.5            # m: a pipe end this close to a point asset is "at" it
  structure_reach: 25.0    # m: a loose end this close to a structure ends at it (WG-01)
  source_reach: 25.0       # m: a tank or reservoir joins a pipe end this close (WG-16)
  diameter_units: mm       # mm, m or in
  diameter_range: [10, 5000]          # mm; outside is WG-09
  allowed_end_assets: [end cap, hydrant, structure]   # add valve to accept ends at valves
  required: [diameter]     # add material to require it (WG-08)
  source_head: 50.0        # m: head of a source with no head or levels mapped
  source_above_ground: 5.0 # m: ... but with a DEM: the ground there plus this
  customer_demand: 0.007   # L/s: a customer with no demand mapped (~600 L/day; set your own)
  customer_reach: 50.0     # m: a customer further than this from every junction adds nothing
  blank_node_values: ["", unknown, none, "null"]       # recorded node ids meaning "none"
  pump_power_kw: 10.0      # kW: a pump with no power mapped
  remove_field: remove     # in a proposal: the field marking features to remove
  dem: ""                  # a terrain raster, or a folder of tiles, for junctions with no
                           # elevation (or --dem); also turns on WG-17
  dem_tolerance: 5.0       # m: a recorded elevation further than this from the DEM (WG-17)

steps:                     # the conversion steps (rules.md#from-gis-to-epanet-the-conversion-steps)
  split_side_joins: true
  cut_rings: true
  join_sources: true
  close_valves: true
  fill_sizes: false        # check: off unless set; export: on unless set to false
  default_size_mm: 100
  fill_elevations: true    # a junction with no elevation (after the DEM) takes the nearest
                           # known one

severity:                  # error, warning, info or off
  WG-04: off
  WG-07: info
```

The defaults (1 mm joins, 1 m near misses, 25 m source reach) were tested on two cities' data;
confirm them with a modeller for your network.
