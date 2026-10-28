"""
PostGIS input (read with psycopg: pyogrio's bundled GDAL has no PostgreSQL driver).

    docker run -d --rm -e POSTGRES_PASSWORD=wf -p 55432:5432 postgis/postgis:16-3.4
    WATERFORGE_POSTGIS="PG:host=localhost port=55432 dbname=postgres user=postgres password=wf" \\
        uv run pytest -m postgis
"""

import os

import geopandas as gpd
import pytest

from tests.helpers import BASE_PIPES, BASE_POINTS, MAPPING, make_network
from waterforge.check import check_network
from waterforge.config import parse_mapping

pytestmark = pytest.mark.postgis
PG = os.environ.get("WATERFORGE_POSTGIS")


@pytest.fixture
def schema(tmp_path):
    """The test network's layers copied into a fresh schema."""
    if not PG:
        pytest.skip("set WATERFORGE_POSTGIS to a PG: connection string")
    import psycopg
    import pyogrio
    import shapely

    points = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}   # one WG-01 to find
    path = make_network(tmp_path, BASE_PIPES, **points)
    name = f"wf_{os.getpid()}"
    with psycopg.connect(PG[3:], autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        conn.execute(f"CREATE SCHEMA {name}")
        for layer, _ in pyogrio.list_layers(path):
            frame = gpd.read_file(path, layer=layer)
            cols = [c for c in frame.columns if c != "geometry"]
            types = {c: "double precision" if frame[c].dtype.kind == "f" else
                     "bigint" if frame[c].dtype.kind == "i" else "text" for c in cols}
            ddl = "".join(f'"{c}" {types[c]}, ' for c in cols)
            conn.execute(f"CREATE TABLE {name}.{layer} ({ddl}geom geometry(Geometry, 2193))")
            marks = "".join("%s, " for _ in cols)
            for _, row in frame.iterrows():
                values = [None if v != v else v for v in (row[c] for c in cols)]
                conn.execute(f"INSERT INTO {name}.{layer} VALUES ({marks}"
                             "ST_SetSRID(ST_GeomFromWKB(%s), 2193))",
                             [*values, shapely.to_wkb(row.geometry)])
    yield name
    with psycopg.connect(PG[3:], autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {name} CASCADE")


def in_schema(name, **changes):
    data = {k: ([{**layer, "layer": f"{name}.{layer['layer']}"} for layer in v]
                if isinstance(v, list) else v) for k, v in MAPPING.items()}
    for kind, values in changes.items():
        data[kind][0].update(values)
    return parse_mapping(data)


def test_check_reads_postgis(schema):
    result = check_network(PG, in_schema(schema))
    assert [i.rule for i in result.issues if i.severity != "info"] == ["WG-01"]
    assert result.epanet.ran and len(result.network.pipes) == 3
    assert result.network.crs.to_epsg() == 2193


def test_where_is_passed_to_postgres(schema):
    result = check_network(PG, in_schema(schema, pipes={"where": "pid <> 3"}), run_epanet=False)
    assert len(result.network.pipes) == 2


def test_missing_table_and_field_are_listed(schema):
    from waterforge.errors import MappingError

    with pytest.raises(MappingError) as caught:
        check_network(PG, in_schema(schema, pipes={"id": "nope"}))
    assert "has no field 'nope'" in str(caught.value)
