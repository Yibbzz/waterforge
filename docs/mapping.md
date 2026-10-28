# The field mapping

A mapping is a YAML file that tells WaterForge which layers, fields and values in a utility's
own data mean which asset types. Any schema works unchanged: nothing has to be moved into a
special layout first. The same file can hold a [rule pack](#rule-packs).

Working examples: [`examples/townsville.yaml`](../examples/townsville.yaml) (Esri-style layers
with numeric type codes) and [`examples/wellington.yaml`](../examples/wellington.yaml) (an
InfoNet export with recorded node ids).

## Layers

Five asset kinds, each a list of layers (a utility with two valve layers lists both):

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

valves:
  - layer: system_valves
    id: ASSET_NUMBER
    closed: {field: NORMALPOSITION, values: [0]}   # which values mean normally closed

hydrants:
  - layer: hydrants
    id: ASSET_NUMBER

structures:                  # tanks, reservoirs, pump stations, ...
  - layer: structures
    id: ASSET_NUMBER
    type:
      field: DESCRIPT
      values: {RESERVOIR: reservoir, WATER_TANK: tank, BOOSTER_PUMP: pump station}
      default: other
  - layer: pump_stations
    type: pump station       # one type for the whole layer
```

- `layer` is required; everything else is optional. A layer can be given by name alone:
  `hydrants: [hydrants]`.
- `where` is passed to GDAL, so it works the same on GeoPackage, PostGIS and FileGDB. Use it to
  keep only assets in use. Removed and abandoned assets left in the layers would otherwise be
  checked as if they were in the ground.
- `id`: without it, features are named by row number. Ids should be unique within a layer; the
  engine warns when they aren't.
- Values are compared as text, ignoring case and surrounding spaces, and `3103.0` matches
  `3103`.
- **Fitting types:** end cap, tee, cross, bend, reducer, joint, tapping band, other.
  **Structure types:** reservoir, tank, pump station, other. Tanks and reservoirs are the
  sources that feed the network.
- Every problem in the mapping is listed at once (exit code 3), including fields a layer
  doesn't have, with a suggestion when only the capitals differ.

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
  source_head: 50.0        # m: head of every source in the model
  blank_node_values: ["", unknown, none, "null"]       # recorded node ids meaning "none"

steps:                     # the conversion steps (rules.md#from-gis-to-epanet-the-conversion-steps)
  split_side_joins: true
  cut_rings: true
  join_sources: true
  close_valves: true
  fill_sizes: false        # check: off unless set; export: on unless set to false
  default_size_mm: 100

severity:                  # error, warning, info or off
  WG-04: off
  WG-07: info
```

The defaults (1 mm joins, 1 m near misses, 25 m source reach) were tested on two cities' data;
confirm them with a modeller for your network.
