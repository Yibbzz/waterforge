"""Sampling a terrain raster: one file, a folder of tiles, windows and point-by-point reads."""

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402

from waterforge import dem  # noqa: E402
from waterforge.errors import InputError  # noqa: E402

X0, Y0 = 1_750_000.0, 5_430_000.0       # NZTM, top-left corner of the west tile


def tile(path, x0, value, nodata_cell=False):
    """A 10 x 10 tile of 10 m cells, all `value` (one cell no data if asked)."""
    values = np.full((10, 10), value, dtype="float32")
    if nodata_cell:
        values[0, 0] = -9999
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1,
                       dtype="float32", crs="EPSG:2193", nodata=-9999,
                       transform=from_origin(x0, Y0, 10, 10)) as out:
        out.write(values, 1)


@pytest.fixture
def tiles(tmp_path):
    folder = tmp_path / "tiles"
    folder.mkdir()
    tile(folder / "a_west.tif", X0, 10.0, nodata_cell=True)
    tile(folder / "b_east.tiff", X0 + 100, 20.0)
    tile(folder / "c_overlap.tif", X0, 30.0)          # under both: only fills gaps
    (folder / "notes.txt").write_text("not a raster")
    return folder


POINTS = ([X0 + 55, X0 + 155, X0 + 5, X0 + 500], [Y0 - 55, Y0 - 55, Y0 - 5, Y0 - 55])


def test_folder_of_tiles_first_tile_with_a_value_wins(tiles):
    got = dem.sample(tiles, *POINTS, "EPSG:2193")
    # west tile, east tile, west tile's no-data cell filled by the overlap tile, outside all
    assert got[:3].tolist() == [10.0, 20.0, 30.0] and np.isnan(got[3])


def test_point_by_point_reads_give_the_same_values(tiles, monkeypatch):
    monkeypatch.setattr(dem, "MAX_WINDOW_CELLS", 0)
    got = dem.sample(tiles, *POINTS, "EPSG:2193")
    assert got[:3].tolist() == [10.0, 20.0, 30.0] and np.isnan(got[3])


def test_points_in_another_crs_are_reprojected(tiles):
    from pyproj import Transformer

    lon, lat = Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True).transform(
        X0 + 155, Y0 - 55)
    assert dem.sample(tiles, [lon], [lat], "EPSG:4326").tolist() == [20.0]


def test_single_file_and_no_points(tiles):
    assert dem.sample(tiles / "b_east.tiff", [X0 + 155], [Y0 - 55], None).tolist() == [20.0]
    assert len(dem.sample(tiles, [], [], None)) == 0


def test_folder_with_no_rasters(tmp_path):
    with pytest.raises(InputError, match="no .tif"):
        dem.sample(tmp_path, [X0], [Y0], None)
