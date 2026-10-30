"""
Elevations from a terrain raster (DEM) for junctions with none recorded (`--dem`).

Needs `waterforge[dem]` (rasterio). Any raster GDAL reads works (GeoTIFF, VRT, ...), in any
CRS: points are reprojected to the raster's. The DEM can also be a folder of tiles (all its
.tif / .tiff / .vrt files, as LINZ and others publish them): each point takes its value from
the first tile that has one. Only the cells under the points are read, so a city-wide 1 m DEM
is fine. Cells with no data, and points outside the raster, give no elevation (the nearest
known one is used then, as without a DEM).
"""

from pathlib import Path

import numpy as np

from .errors import InputError

RASTER_SUFFIXES = (".tif", ".tiff", ".vrt")
MAX_WINDOW_CELLS = 50_000_000      # read one window up to this size, else point by point


def rasters(path) -> list[Path]:
    """The raster file, or the rasters in a folder (sorted)."""
    p = Path(path)
    if p.is_dir():
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in RASTER_SUFFIXES)
        if not files:
            raise InputError(f"The DEM folder {p} has no .tif, .tiff or .vrt files.")
        return files
    return [p]


def sample(path, xs, ys, crs) -> np.ndarray:
    """The raster's first band at each point (NaN where there is no value)."""
    try:
        import rasterio
    except ImportError as exc:
        raise InputError("Sampling a DEM needs rasterio: install waterforge[dem] "
                         "(pip install 'waterforge[dem]').") from exc

    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    out = np.full(len(xs), np.nan)
    for file in rasters(path):
        todo = np.flatnonzero(np.isnan(out))
        if len(todo) == 0:
            break
        try:
            src = rasterio.open(file)
        except Exception as exc:  # noqa: BLE001 - rasterio's message says why
            raise InputError(f"Can't open the DEM {file}: {exc}") from exc
        with src:
            if src.crs is None:
                raise InputError(f"The DEM {file} has no coordinate reference system.")
            out[todo] = _sample_one(src, xs[todo], ys[todo], crs)
    return out


def _sample_one(src, xs, ys, crs) -> np.ndarray:
    """One open raster's values at the points, reading only the cells around them."""
    import rasterio
    from pyproj import Transformer

    if crs is not None and src.crs != crs:
        xs, ys = Transformer.from_crs(crs, src.crs, always_xy=True).transform(xs, ys)
        xs, ys = np.asarray(xs), np.asarray(ys)
    rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
    rows, cols = np.asarray(rows), np.asarray(cols)
    out = np.full(len(xs), np.nan)
    inside = np.flatnonzero((rows >= 0) & (rows < src.height)
                            & (cols >= 0) & (cols < src.width))
    if len(inside) == 0:
        return out
    r, c = rows[inside], cols[inside]
    r0, c0 = r.min(), c.min()
    height, width = r.max() + 1 - r0, c.max() + 1 - c0
    if height * width <= MAX_WINDOW_CELLS:
        band = src.read(1, window=rasterio.windows.Window(c0, r0, width, height), masked=True)
        values = band[r - r0, c - c0]
    else:
        read = [v for v in src.sample(zip(xs[inside], ys[inside]), indexes=1, masked=True)]
        values = np.ma.array([v.data[0] for v in read],
                             mask=[np.ma.getmaskarray(v)[0] for v in read])
    out[inside] = np.ma.filled(values.astype(float), np.nan)
    return out
