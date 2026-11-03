"""
Read a utility's layers through the mapping into one common layout.

    network = read_network("existing.gpkg", mapping)

Any GDAL source works (GeoPackage, Shapefile folder, File Geodatabase,
"PG:dbname=..." for PostGIS). Input is read-only: nothing is written back.

The result holds three tables:
  pipes   lines: layer, fid (the mapping's id), part (of a multi-part line),
          diameter_mm, diameter_text, material, roughness, start_node, end_node
  points  every point asset: asset (junction / fitting / valve / hydrant / pump /
          structure), layer, fid, asset_type (end cap, prv, reservoir, ...), closed,
          and the number fields (elevation, demand, setting, power, head, levels)
  links   valves and pumps drawn as lines instead of points
plus the issues found while reading (WG-06 geometry, WG-08 missing
attributes, WG-09 values the mapping can't read). In a proposal, `remove`
marks features to remove.
"""

import logging
import re
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from . import postgis
from .config import (
    DIAMETER_UNITS,
    LINK_KINDS,
    NUMBER_FIELDS,
    LayerSpec,
    Mapping,
    normalise_value,
)
from .errors import InputError, MappingError
from .issues import Issue, feature

log = logging.getLogger(__name__)

ASSET_OF_KIND = {"junctions": "junction", "fittings": "fitting", "valves": "valve",
                 "hydrants": "hydrant", "pumps": "pump", "structures": "structure"}
NUMBER_COLUMNS = list(NUMBER_FIELDS)
PIPE_COLUMNS = ["layer", "fid", "part", "diameter_mm", "diameter_text", "material", "roughness",
                "start_node", "end_node", "pipe_class", "remove", "geometry"]
POINT_COLUMNS = ["asset", "layer", "fid", "asset_type", "closed", *NUMBER_COLUMNS, "remove",
                 "geometry"]
LINK_COLUMNS = ["asset", "layer", "fid", "asset_type", "closed", "setting", "power",
                "design_flow", "design_head", "remove", "geometry"]
CUSTOMER_COLUMNS = ["layer", "fid", "demand", "geometry"]
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_YES = {"yes", "y", "true", "1", "t"}


@dataclass
class Network:
    pipes: gpd.GeoDataFrame
    points: gpd.GeoDataFrame     # point assets (junctions, fittings, valves, ...)
    links: gpd.GeoDataFrame      # valves and pumps drawn as lines
    crs: object
    issues: list[Issue] = field(default_factory=list)
    customers: gpd.GeoDataFrame | None = None   # layer, fid, demand (L/s or NaN), geometry

    @property
    def sources(self) -> gpd.GeoDataFrame:
        p = self.points
        return p[(p.asset == "structure") & p.asset_type.isin(["reservoir", "tank"])]


def read_network(source: str, mapping: Mapping, proposal: bool = False, crs=None) -> Network:
    """
    Read every mapped layer; raise MappingError listing all missing layers or fields.

    proposal=True reads a proposed change: layers may be missing (nothing proposed there),
    and the `remove` field (settings.remove_field) marks features to remove. `crs` forces
    the CRS to work in (the existing network's, for a proposal).
    """
    present, geometry_columns = _check_source(source, mapping, proposal)
    specs = [s for s in mapping.layers if s.layer in present]
    issues: list[Issue] = []
    frames = [_read_layer(source, spec, mapping, *present[spec.layer],
                          geometry_columns.get(spec.layer), proposal) for spec in specs]
    if crs is None:
        crs = _target_crs([f for f, spec in zip(frames, specs) if spec.kind == "pipes"],
                          mapping)
    pipes, points, links, customers = [], [], [], []
    for frame, spec in zip(frames, specs):
        frame = _to_crs(frame, crs, spec.layer)
        frame["remove"] = _remove(frame, present[spec.layer][0])
        if spec.kind == "pipes":
            pipes.append(_pipes(frame, spec, mapping, issues))
        elif spec.kind == "customers":
            frame = frame[~frame.geometry.is_empty]
            customers.append(pd.DataFrame({
                "layer": spec.layer, "fid": _ids(frame, spec),
                "demand": _numbers(frame, spec, "demand", issues),
                "geometry": frame.geometry.values}))
        else:
            p, k = _points(frame, spec, issues)
            points.append(p)
            links.append(k)
    network = Network(_frame(pipes, PIPE_COLUMNS, crs), _frame(points, POINT_COLUMNS, crs),
                      _frame(links, LINK_COLUMNS, crs), crs, issues,
                      _frame(customers, CUSTOMER_COLUMNS, crs))
    log.info("Read %s pipes (%.1f km), %s point assets%s%s", f"{len(network.pipes):,}",
             network.pipes.length.sum() / 1000, f"{len(network.points):,}",
             f", {len(network.links)} valves / pumps drawn as lines" if len(network.links)
             else "", " (proposed change)" if proposal else "")
    if len(network.customers):
        log.info("Read %s customers", f"{len(network.customers):,}")
    return network


def _frame(parts, columns, crs):
    parts = [p for p in parts if len(p)]
    if not parts:
        return gpd.GeoDataFrame({c: [] for c in columns}, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True)[columns], geometry="geometry",
                            crs=crs)


def _check_source(source, mapping, proposal):
    """
    ({layer: (remove field or None, {mapping field: actual field})} for the layers present,
    {layer: geometry column} for PostGIS); problems raised, all at once.

    A field matches regardless of capitals when only one field does: loading into
    PostgreSQL usually lowercases names (ASSET_NUMBER -> asset_number).
    """
    geometry_columns = {}
    if postgis.is_postgis(source):
        geometry_columns = postgis.list_layers(source)
        layers = set(geometry_columns)
    else:
        try:
            layers = {name for name, _ in pyogrio.list_layers(source)}
        except Exception as exc:  # noqa: BLE001 - GDAL's own message says why
            raise InputError(f"Can't open {source}: {exc}") from exc
    problems, present = [], {}
    for spec in mapping.layers:
        if spec.layer not in layers:
            if not proposal:
                problems.append(f"{spec.kind}: layer '{spec.layer}' is not in {source} "
                                f"(it has: {', '.join(sorted(layers))})")
            continue
        have = set(postgis.fields(source, spec.layer) if geometry_columns
                   else pyogrio.read_info(source, layer=spec.layer)["fields"])
        by_lower = {}
        for f in have:
            by_lower.setdefault(f.lower(), []).append(f)
        names = {}
        for name in spec.source_fields():
            if name in have:
                names[name] = name
            elif len(by_lower.get(name.lower(), [])) == 1:
                names[name] = by_lower[name.lower()][0]
            else:
                problems.append(f"{spec.kind}: layer '{spec.layer}' has no field '{name}'")
        remove = by_lower.get(mapping.settings.remove_field.lower(), [None])[0] \
            if proposal else None
        # a layer may be mapped more than once (pipes and customers from one pipe layer)
        names = {**present.get(spec.layer, (None, {}))[1], **names}
        present[spec.layer] = (remove, names)
    if problems:
        raise MappingError(problems, source="proposal" if proposal else "mapping")
    if proposal and not present:
        raise InputError(f"The proposal {source} has none of the mapped layers "
                         f"({', '.join(sorted({s.layer for s in mapping.layers}))}).")
    return present, geometry_columns


def _read_layer(source, spec: LayerSpec, mapping: Mapping, remove=None, names=None,
                geometry_column=None, proposal=False) -> gpd.GeoDataFrame:
    """One layer's mapped fields, renamed to the mapping's spelling.

    A proposal doesn't apply `where`: everything proposed is meant to be in use, and the
    proposal's file needn't have the fields the filter names.
    """
    names = names or {f: f for f in spec.source_fields()}
    columns = list(dict.fromkeys([names[f] for f in spec.source_fields()]
                                 + ([remove] if remove else [])))
    where = None if proposal else spec.where
    try:
        if geometry_column:
            frame = postgis.read_layer(source, spec.layer, geometry_column, columns, where)
        else:
            frame = gpd.read_file(source, layer=spec.layer, where=where, columns=columns,
                                  engine="pyogrio")
        frame = frame.rename(columns={actual: name for name, actual in names.items()
                                      if actual != name})
    except Exception as exc:  # noqa: BLE001
        if spec.where:
            raise MappingError([f"{spec.kind}: layer '{spec.layer}': can't apply "
                                f"where: {spec.where!r} ({exc})"]) from exc
        raise InputError(f"Can't read layer '{spec.layer}': {exc}") from exc
    if frame.crs is None and mapping.crs:
        frame = frame.set_crs(mapping.crs)
    frame = frame[frame.geometry.notna()].copy()
    frame["geometry"] = shapely.force_2d(frame.geometry.values)
    return frame


def _remove(frame, field_name):
    if not field_name or field_name not in frame:
        return False
    return frame[field_name].map(lambda v: v is not None and v == v
                                 and normalise_value(v) in _YES).astype(bool)


def _target_crs(pipe_frames, mapping):
    crs = next((f.crs for f in pipe_frames if f.crs is not None), None)
    if crs is None:
        raise InputError("The pipe layer has no coordinate reference system. Set one in the "
                         "data, or give 'crs:' in the mapping (for example crs: EPSG:7855).",
                         guide="mapping.md#crs")
    if crs.is_geographic or crs.to_epsg() == 3857:
        joined = pd.concat([f.to_crs(4326) for f in pipe_frames])
        utm = gpd.GeoDataFrame(joined, geometry="geometry", crs=4326).estimate_utm_crs()
        log.warning("The pipes are in %s, which isn't in metres; working in %s instead. "
                    "Distances such as the 1 mm tolerance need a projected CRS.", crs.name,
                    utm.name)
        return utm
    unit = crs.axis_info[0].unit_name if crs.axis_info else "metre"
    if unit not in ("metre", "meter"):
        raise InputError(f"The pipes' CRS ({crs.name}) is in {unit}, not metres. Reproject "
                         "the data to a projected CRS in metres.", guide="mapping.md#crs")
    return crs


def _to_crs(frame, crs, layer):
    if frame.crs is None:
        raise InputError(f"Layer '{layer}' has no coordinate reference system; set 'crs:' "
                         "in the mapping.", guide="mapping.md#crs")
    return frame if frame.crs == crs else frame.to_crs(crs)


def _ids(frame, spec):
    if spec.id is None:
        return pd.Series(np.arange(1, len(frame) + 1), index=frame.index, dtype=object)
    ids = frame[spec.id]
    if pd.api.types.is_float_dtype(ids) and ids.dropna().apply(float.is_integer).all():
        ids = ids.astype("Int64")
    ids = ids.astype(object).where(ids.notna(), None)
    missing = ids.isna()
    if missing.any():
        ids[missing] = [f"row {i}" for i in np.flatnonzero(missing.values) + 1]
    dupes = ids[ids.duplicated()].unique()
    if len(dupes):
        log.warning("Layer '%s': %d %s values are used more than once (e.g. %s); issues name "
                    "those features ambiguously.", spec.layer, len(dupes), spec.id,
                    ", ".join(map(str, dupes[:3])))
    return ids


def _pipes(frame, spec, mapping, issues):
    frame = frame.assign(layer=spec.layer, fid=_ids(frame, spec))
    lines = frame.geometry.geom_type.isin(["LineString", "MultiLineString"])
    if not lines.all():
        bad = frame.geometry.geom_type[~lines].iloc[0]
        raise InputError(f"Pipe layer '{spec.layer}' has {bad} geometry; pipes must be lines.")
    for row in frame[frame.geometry.is_empty].itertuples():
        issues.append(Issue("WG-06", "Empty geometry: left out of the model",
                            [feature(spec.layer, row.fid)], shapely.Point()))
    frame = frame[~frame.geometry.is_empty]
    parts = pd.Series(shapely.get_num_geometries(frame.geometry.values), index=frame.index)
    multi = frame[parts > 1]
    for row, n, first in zip(multi.itertuples(), parts[multi.index],
                             shapely.get_point(shapely.get_geometry(multi.geometry.values, 0), 0)):
        issues.append(Issue("WG-06", f"Multi-part line with {n} parts: each part is treated as "
                            "its own pipe", [feature(spec.layer, row.fid)], first,
                            severity="warning"))
    frame = frame.explode(index_parts=True)
    frame["part"] = frame.index.get_level_values(-1) if len(frame) else 0
    frame = frame.reset_index(drop=True)

    size = _diameters(frame, spec, mapping, issues)
    out = pd.DataFrame({
        "layer": frame.layer, "fid": frame.fid, "part": frame.part.astype(int),
        "diameter_mm": size[0], "diameter_text": size[1],
        "material": frame[spec.material] if spec.material else None,
        "roughness": _numbers(frame, spec, "roughness", issues),
        "start_node": frame[spec.start_node].astype(object) if spec.start_node else None,
        "end_node": frame[spec.end_node].astype(object) if spec.end_node else None,
        "pipe_class": _pipe_classes(frame, spec, issues),
        "remove": frame["remove"].to_numpy() if "remove" in frame else False,
        "geometry": frame.geometry.values,
    })
    if "material" in mapping.settings.required and spec.material:
        for row in out[out.material.isna()].drop_duplicates("fid").itertuples():
            issues.append(Issue("WG-08", "No material recorded", [feature(row.layer, row.fid)],
                                shapely.get_point(row.geometry, 0)))
    return out


def _pipe_classes(frame, spec, issues):
    """Each pipe's class (trunk, distribution, ...: the mapping's own names), or None."""
    m = spec.pipe_class
    if m is None:
        return None
    if m.field is None:
        return pd.Series(m.default, index=frame.index, dtype=object)
    values = frame[m.field].map(normalise_value)
    out = values.map(m.values)
    unknown = out.isna() & frame[m.field].notna()
    if m.default is None:
        for _, group in frame[unknown].groupby(values[unknown]):
            issues.append(Issue(
                "WG-09", f"{m.field} = '{group[m.field].iloc[0]}' isn't in the mapping's pipe "
                f"classes ({group.fid.nunique()} pipes); no class given, so the connection "
                "rules (WG-18) skip them",
                [feature(spec.layer, f) for f in group.fid.unique()[:200]],
                shapely.get_point(group.geometry.iloc[0], 0), severity="warning"))
    else:
        out = out.fillna(m.default)  # values not listed, and blanks
    return out.astype(object).where(out.notna(), None)


def _diameters(frame, spec, mapping, issues):
    """Diameters in mm, and the text they were read from where it wasn't a plain number."""
    n = len(frame)
    if spec.diameter is None:
        return np.full(n, np.nan), [None] * n
    raw = frame[spec.diameter]
    factor = DIAMETER_UNITS[mapping.settings.diameter_units]
    low, high = mapping.settings.diameter_range
    text = raw.astype("string").str.strip()
    plain = pd.to_numeric(raw, errors="coerce")
    found = text.str.findall(_NUMBER)
    first = found.str[0].str.replace(",", ".")
    from_text = pd.to_numeric(first, errors="coerce")
    size = plain.where(plain.notna(), from_text) * factor
    missing = text.isna() | (text == "")
    starts = shapely.get_point(frame.geometry.values, 0)
    report = (frame.part == 0).to_numpy()  # one issue per feature, not per part
    for i in np.flatnonzero(missing & report):
        issues.append(Issue("WG-08", "No diameter recorded", [feature(spec.layer, frame.fid[i])],
                            starts[i]))
    unreadable = ~missing & size.isna()
    for i in np.flatnonzero(unreadable & report):
        issues.append(Issue("WG-09", f"Diameter '{text[i]}' can't be read as a number",
                            [feature(spec.layer, frame.fid[i])], starts[i]))
    outside = size.notna() & ((size < low) | (size > high))
    for i in np.flatnonzero(outside & report):
        issues.append(Issue("WG-09", f"Diameter {size[i]:g} mm is outside {low:g}-{high:g} mm",
                            [feature(spec.layer, frame.fid[i])], starts[i],
                            data={"diameter_mm": float(size[i])}))
    loose = plain.isna() & from_text.notna() & ((found.str.len() > 1) | (first != text))
    for i in np.flatnonzero(loose & ~outside & report):
        issues.append(Issue("WG-09", f"Diameter '{text[i]}' read as {size[i]:g} mm",
                            [feature(spec.layer, frame.fid[i])], starts[i], severity="info"))
    size = size.where(~outside)
    keep_text = text.where(loose | unreadable, None).astype(object)
    return size.to_numpy(dtype=float), keep_text.where(keep_text.notna(), None).tolist()


def _numbers(frame, spec, name, issues):
    """A number field as floats (NaN where empty); values that aren't numbers are WG-09."""
    source = getattr(spec, name)
    if source is None:
        return np.full(len(frame), np.nan)
    raw = frame[source]
    values = pd.to_numeric(raw, errors="coerce")
    bad = values.isna() & raw.notna() & (raw.astype("string").str.strip() != "")
    if bad.any():
        rows = frame[bad.to_numpy()]
        issues.append(Issue(
            "WG-09", f"{source} = '{raw[bad].iloc[0]}' isn't a number ({int(bad.sum())} "
            f"feature(s) in layer '{spec.layer}'); treated as missing",
            [feature(spec.layer, f) for f in rows.fid[:200]], _first_point(rows.geometry.iloc[0]),
            severity="warning", data={"field": source}))
    return values.to_numpy(dtype=float)


def _first_point(geometry):
    return geometry if geometry.geom_type == "Point" else shapely.get_point(geometry, 0)


def _points(frame, spec, issues):
    """(points, links): valves and pumps may be drawn as lines (links); the rest are points."""
    asset = ASSET_OF_KIND[spec.kind]
    frame = frame.assign(layer=spec.layer, fid=_ids(frame, spec))
    kinds = frame.geometry.geom_type
    allowed = ["Point", "MultiPoint"] + (["LineString", "MultiLineString"]
                                         if spec.kind in LINK_KINDS else [])
    if not kinds.isin(allowed).all():
        bad = kinds[~kinds.isin(allowed)].iloc[0]
        what = "points or lines" if spec.kind in LINK_KINDS else "points"
        raise InputError(f"Layer '{spec.layer}' ({spec.kind}) has {bad} geometry; "
                         f"{spec.kind} must be {what}.")
    frame = frame[~frame.geometry.is_empty]
    multi = frame[(frame.geometry.geom_type == "MultiLineString")
                  & (shapely.get_num_geometries(frame.geometry.values) > 1)]
    for row in multi.itertuples():
        issues.append(Issue("WG-06", f"Multi-part line for {asset} {row.fid}: each part is "
                            "treated as its own link", [feature(spec.layer, row.fid)],
                            shapely.get_point(shapely.get_geometry(row.geometry, 0), 0),
                            severity="warning"))
    frame = frame.explode(index_parts=False).reset_index(drop=True)
    default = {"valves": "isolation", "pumps": "pump"}.get(spec.kind, asset)
    kind = pd.Series(default, index=frame.index)
    if spec.type is not None and spec.type.field is None:
        kind = pd.Series(spec.type.default, index=frame.index)
    elif spec.type is not None:
        values = frame[spec.type.field].map(normalise_value)
        kind = values.map(spec.type.values)
        unknown = kind.isna() & frame[spec.type.field].notna()
        fallback = spec.type.default or ("isolation" if spec.kind == "valves" else "other")
        if spec.type.default is None:
            for _, group in frame[unknown].groupby(values[unknown]):
                issues.append(Issue(
                    "WG-09", f"{spec.type.field} = '{group[spec.type.field].iloc[0]}' isn't in "
                    f"the mapping ({len(group)} features); treated as '{fallback}'",
                    [feature(spec.layer, f) for f in group.fid[:200]],
                    _first_point(group.geometry.iloc[0]), severity="warning"))
        kind = kind.fillna(fallback)
    closed = (frame[spec.closed.field].map(normalise_value).isin(spec.closed.values)
              if spec.closed is not None else pd.Series(False, index=frame.index))
    out = pd.DataFrame({"asset": asset, "layer": spec.layer, "fid": frame.fid,
                        "asset_type": kind.to_numpy(), "closed": closed.to_numpy(dtype=bool),
                        **{name: _numbers(frame, spec, name, issues) for name in NUMBER_COLUMNS},
                        "remove": frame["remove"].to_numpy() if "remove" in frame else False,
                        "geometry": frame.geometry.values})
    is_line = out.geometry.map(lambda g: g.geom_type == "LineString").to_numpy(dtype=bool)
    return out[~is_line], out[is_line][LINK_COLUMNS]
