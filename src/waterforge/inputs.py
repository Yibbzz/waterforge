"""
Read a utility's layers through the mapping into one common layout.

    network = read_network("existing.gpkg", mapping)

Any GDAL source works (GeoPackage, Shapefile folder, File Geodatabase,
"PG:dbname=..." for PostGIS). Input is read-only: nothing is written back.

The result holds two tables:
  pipes   lines: layer, fid (the mapping's id), part (of a multi-part line),
          diameter_mm, diameter_text, material, start_node, end_node
  points  every point asset: asset (fitting / valve / hydrant / structure),
          layer, fid, asset_type (end cap, tee, reservoir, ...), closed
plus the issues found while reading (WG-06 geometry, WG-08 missing
attributes, WG-09 values the mapping can't read).
"""

import logging
import re
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from .config import DIAMETER_UNITS, LayerSpec, Mapping, normalise_value
from .errors import InputError, MappingError
from .issues import Issue, feature

log = logging.getLogger(__name__)

ASSET_OF_KIND = {"fittings": "fitting", "valves": "valve", "hydrants": "hydrant",
                 "structures": "structure"}
PIPE_COLUMNS = ["layer", "fid", "part", "diameter_mm", "diameter_text", "material",
                "start_node", "end_node", "geometry"]
POINT_COLUMNS = ["asset", "layer", "fid", "asset_type", "closed", "geometry"]
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class Network:
    pipes: gpd.GeoDataFrame
    points: gpd.GeoDataFrame
    crs: object
    issues: list[Issue] = field(default_factory=list)

    @property
    def sources(self) -> gpd.GeoDataFrame:
        p = self.points
        return p[(p.asset == "structure") & p.asset_type.isin(["reservoir", "tank"])]


def read_network(source: str, mapping: Mapping) -> Network:
    """Read every mapped layer; raise MappingError listing all missing layers or fields."""
    _check_source(source, mapping)
    issues: list[Issue] = []
    frames = [_read_layer(source, spec, mapping) for spec in mapping.layers]
    crs = _target_crs([f for f, spec in zip(frames, mapping.layers) if spec.kind == "pipes"],
                      mapping)
    pipes, points = [], []
    for frame, spec in zip(frames, mapping.layers):
        frame = _to_crs(frame, crs, spec.layer)
        if spec.kind == "pipes":
            pipes.append(_pipes(frame, spec, mapping, issues))
        else:
            points.append(_points(frame, spec, issues))
    pipes = gpd.GeoDataFrame(pd.concat(pipes, ignore_index=True), geometry="geometry", crs=crs)
    points = (gpd.GeoDataFrame(pd.concat(points, ignore_index=True), geometry="geometry", crs=crs)
              if points else gpd.GeoDataFrame(columns=POINT_COLUMNS, geometry="geometry", crs=crs))
    log.info("Read %s pipes (%.1f km) and %s point assets", f"{len(pipes):,}",
             pipes.length.sum() / 1000, f"{len(points):,}")
    return Network(pipes, points, crs, issues)


def _check_source(source, mapping):
    try:
        layers = {name for name, _ in pyogrio.list_layers(source)}
    except Exception as exc:  # noqa: BLE001 - GDAL's own message says why
        raise InputError(f"Can't open {source}: {exc}") from exc
    problems = []
    for spec in mapping.layers:
        if spec.layer not in layers:
            problems.append(f"{spec.kind}: layer '{spec.layer}' is not in {source} "
                            f"(it has: {', '.join(sorted(layers))})")
            continue
        have = set(pyogrio.read_info(source, layer=spec.layer)["fields"])
        lower = {f.lower(): f for f in have}
        for name in spec.source_fields():
            if name not in have:
                hint = f" (did you mean '{lower[name.lower()]}'?)" if name.lower() in lower else ""
                problems.append(f"{spec.kind}: layer '{spec.layer}' has no field '{name}'{hint}")
    if problems:
        raise MappingError(problems)


def _read_layer(source, spec: LayerSpec, mapping: Mapping) -> gpd.GeoDataFrame:
    try:
        frame = gpd.read_file(source, layer=spec.layer, where=spec.where,
                              columns=spec.source_fields(), engine="pyogrio")
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
        "start_node": frame[spec.start_node].astype(object) if spec.start_node else None,
        "end_node": frame[spec.end_node].astype(object) if spec.end_node else None,
        "geometry": frame.geometry.values,
    })
    if "material" in mapping.settings.required and spec.material:
        for row in out[out.material.isna()].drop_duplicates("fid").itertuples():
            issues.append(Issue("WG-08", "No material recorded", [feature(row.layer, row.fid)],
                                shapely.get_point(row.geometry, 0)))
    return out


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


def _points(frame, spec, issues):
    asset = ASSET_OF_KIND[spec.kind]
    frame = frame.assign(layer=spec.layer, fid=_ids(frame, spec))
    kinds = frame.geometry.geom_type
    if not kinds.isin(["Point", "MultiPoint"]).all():
        bad = kinds[~kinds.isin(["Point", "MultiPoint"])].iloc[0]
        raise InputError(f"Layer '{spec.layer}' ({spec.kind}) has {bad} geometry; "
                         "point assets must be points.")
    frame = frame[~frame.geometry.is_empty].explode(index_parts=False).reset_index(drop=True)
    kind = None
    if spec.type is not None and spec.type.field is None:
        kind = pd.Series(spec.type.default, index=frame.index)
    elif spec.type is not None:
        values = frame[spec.type.field].map(normalise_value)
        kind = values.map(spec.type.values)
        unknown = kind.isna() & frame[spec.type.field].notna()
        if spec.type.default is None:
            for _, group in frame[unknown].groupby(values[unknown]):
                issues.append(Issue(
                    "WG-09", f"{spec.type.field} = '{group[spec.type.field].iloc[0]}' isn't in "
                    f"the mapping ({len(group)} features); treated as 'other'",
                    [feature(spec.layer, f) for f in group.fid], group.geometry.iloc[0],
                    severity="warning"))
        kind = kind.fillna(spec.type.default or "other")
    closed = (frame[spec.closed.field].map(normalise_value).isin(spec.closed.values)
              if spec.closed is not None else False)
    return pd.DataFrame({"asset": asset, "layer": spec.layer, "fid": frame.fid,
                         "asset_type": kind if kind is not None else asset, "closed": closed,
                         "geometry": frame.geometry.values})
