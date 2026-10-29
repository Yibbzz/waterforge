"""
Elevations from a terrain raster (DEM) for junctions with none recorded (`--dem`).

Needs `waterforge[dem]` (rasterio). Any raster GDAL reads works (GeoTIFF, VRT, ...), in any
CRS: points are reprojected to the raster's. Cells with no data, and points outside the
raster, give no elevation (the nearest known one is used then, as without a DEM).
"""

import numpy as np

from .errors import InputError


def sample(path, xs, ys, crs) -> np.ndarray:
    """The raster's first band at each point (NaN where there is no value)."""
    try:
        import rasterio
    except ImportError as exc:
        raise InputError("Sampling a DEM needs rasterio: install waterforge[dem] "
                         "(pip install 'waterforge[dem]').") from exc
    from pyproj import Transformer

    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    if len(xs) == 0:
        return np.zeros(0)
    try:
        src = rasterio.open(path)
    except Exception as exc:  # noqa: BLE001 - rasterio's message says why
        raise InputError(f"Can't open the DEM {path}: {exc}") from exc
    with src:
        if src.crs is None:
            raise InputError(f"The DEM {path} has no coordinate reference system.")
        if crs is not None and src.crs != crs:
            xs, ys = Transformer.from_crs(crs, src.crs, always_xy=True).transform(xs, ys)
        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
        rows, cols = np.asarray(rows), np.asarray(cols)
        inside = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
        out = np.full(len(xs), np.nan)
        if inside.any():
            band = src.read(1, masked=True)
            values = band[rows[inside], cols[inside]]
            out[inside] = np.ma.filled(values.astype(float), np.nan)
    return out
