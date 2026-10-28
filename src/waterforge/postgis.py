"""
Read layers from PostGIS with psycopg (`pip install waterforge[postgis]`).

pyogrio's bundled GDAL has no PostgreSQL driver, so a "PG:..." source is read here instead:

    waterforge check --network "PG:host=db dbname=water user=gis" --mapping utility.yaml

The connection string after "PG:" is a libpq one (host, port, dbname, user, password; a
password can also come from ~/.pgpass or PGPASSWORD). A layer is a table or view with a
geometry column, named "table" (search path) or "schema.table". The mapping's `where` is
passed to PostgreSQL as is. Read-only: only SELECTs are sent, in a read-only transaction.
"""

import geopandas as gpd
import pandas as pd
import shapely

from .errors import InputError


def is_postgis(source) -> bool:
    return isinstance(source, str) and (source.startswith("PG:")
                                        or source.startswith("postgresql://"))


def _connect(source):
    try:
        import psycopg
    except ImportError as exc:
        raise InputError("Reading PostGIS needs psycopg: install waterforge[postgis] "
                         "(pip install 'waterforge[postgis]').") from exc
    conninfo = source[3:] if source.startswith("PG:") else source
    try:
        conn = psycopg.connect(conninfo, connect_timeout=15)
    except Exception as exc:  # noqa: BLE001 - psycopg's message says why
        raise InputError(f"Can't connect to PostGIS: {exc}") from exc
    conn.read_only = True
    return conn


def _split(layer):
    schema, _, table = layer.rpartition(".")
    return (schema or None), table


def list_layers(source) -> dict:
    """{layer name: (geometry column, declared SRID)} for every table or view with geometry."""
    with _connect(source) as conn:
        rows = conn.execute(
            "SELECT f_table_schema, f_table_name, f_geometry_column, srid FROM geometry_columns"
        ).fetchall()
        search = conn.execute("SELECT current_schemas(false)").fetchone()[0]
    layers = {}
    for schema, table, column, srid in rows:
        layers[f"{schema}.{table}"] = (column, srid)
        if schema in search:
            layers.setdefault(table, (column, srid))
    return layers


def fields(source, layer) -> list[str]:
    schema, table = _split(layer)
    with _connect(source) as conn:
        if schema is None:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns c "
                "WHERE table_name = %s AND table_schema = ANY(current_schemas(false))",
                (table,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s", (schema, table)).fetchall()
    return [r[0] for r in rows]


def read_layer(source, layer, geometry, columns, where=None) -> gpd.GeoDataFrame:
    """The layer's `columns` and geometry, filtered by `where`, with its CRS.

    `geometry` is (column, declared SRID) from list_layers; a table without a declared SRID
    takes the SRID of its rows.
    """
    from psycopg import sql

    geometry_column, declared = geometry
    schema, table = _split(layer)
    name = sql.Identifier(schema, table) if schema else sql.Identifier(table)
    geom = sql.Identifier(geometry_column)
    query = sql.SQL("SELECT {cols}ST_AsBinary({geom}) AS __geom, ST_SRID({geom}) AS __srid "
                    "FROM {name}").format(
        cols=sql.SQL("").join(sql.SQL("{}, ").format(sql.Identifier(c)) for c in columns),
        geom=geom, name=name)
    if where:
        query = query + sql.SQL(" WHERE ") + sql.SQL(where)  # the user's own mapping
    with _connect(source) as conn:
        try:
            cursor = conn.execute(query)
        except Exception as exc:  # noqa: BLE001
            raise InputError(f"Can't read layer '{layer}' from PostGIS: {exc}") from exc
        rows = cursor.fetchall()
    frame = pd.DataFrame(rows, columns=list(columns) + ["__geom", "__srid"])
    srids = {s for s in frame["__srid"] if s} or ({declared} if declared else set())
    shapes = shapely.from_wkb([bytes(g) if g is not None else None for g in frame["__geom"]])
    crs = f"EPSG:{srids.pop()}" if len(srids) == 1 else None
    return gpd.GeoDataFrame(frame[list(columns)], geometry=shapes, crs=crs)
