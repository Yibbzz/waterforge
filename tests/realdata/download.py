"""
Download the two test cities' water networks from their public ArcGIS services.

    uv run python -m tests.realdata.download data          # both, into data/
    uv run python -m tests.realdata.download data townsville

Townsville City Council open data: CC BY 4.0, EPSG:7855.
Wellington Water open data (WCC 3 Waters Asset Data, exported weekly from InfoNet): credit
Wellington Water; not for commercial use or detailed engineering design. EPSG:2193.

The data is never committed. Layer names match examples/<city>.yaml.
"""

import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd

log = logging.getLogger("waterforge.download")

PAGE = 2000
TOWNSVILLE = "https://services6.arcgis.com/3VCE6mezZtwKJeIR/arcgis/rest/services"
WELLINGTON = ("https://services7.arcgis.com/2ECs938g489DMWjt/arcgis/rest/services/"
              "WCC_3Waters_Asset_Data/FeatureServer")
CITIES = {
    "townsville": ("EPSG:7855", "OBJECTID", {
        "mains": f"{TOWNSVILLE}/Asset_Water_Water_Main_GDA2020/FeatureServer/15",
        "system_valves": f"{TOWNSVILLE}/Asset_Water_System_Valve_GDA2020/FeatureServer/12",
        "control_valves": f"{TOWNSVILLE}/Asset_Water_Control_Valve_GDA2020/FeatureServer/3",
        "hydrants": f"{TOWNSVILLE}/Asset_Water_Hydrant_GDA2020/FeatureServer/5",
        "fittings": f"{TOWNSVILLE}/Asset_Water_Fitting_GDA2020/FeatureServer/4",
        "structures": f"{TOWNSVILLE}/Asset_Water_Network_Structure_GDA2020/FeatureServer/7",
    }),
    "wellington": ("EPSG:2193", "objectid", {
        "pipes": f"{WELLINGTON}/13", "valves": f"{WELLINGTON}/8",
        "fittings": f"{WELLINGTON}/12", "hydrants": f"{WELLINGTON}/1",
        "tanks": f"{WELLINGTON}/0", "pump_stations": f"{WELLINGTON}/9",
    }),
}


def fetch(layer_url: str, crs: str, order: str) -> gpd.GeoDataFrame:
    """Every feature of an ArcGIS REST layer, page by page."""
    features, offset = [], 0
    epsg = crs.split(":")[1]
    while True:
        query = urllib.parse.urlencode({
            "where": "1=1", "outFields": "*", "outSR": epsg, "f": "geojson",
            "orderByFields": order, "resultOffset": offset, "resultRecordCount": PAGE})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(f"{layer_url}/query?{query}", timeout=180) as reply:
                    page = json.load(reply)
                if "features" not in page:
                    raise ValueError(str(page)[:300])
                break
            except (OSError, ValueError):
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        features += page["features"]
        log.info("  %s: %d", layer_url.rsplit("/", 1)[-1], len(features))
        if len(page["features"]) < PAGE:
            return gpd.GeoDataFrame.from_features(features, crs=crs)
        offset += PAGE


def download(folder: Path, name: str) -> Path:
    crs, order, layers = CITIES[name]
    out = folder / f"{name}.gpkg"
    out.unlink(missing_ok=True)
    for layer, url in layers.items():
        frame = fetch(url, crs, order)
        frame.to_file(out, layer=layer, driver="GPKG", engine="pyogrio")
        log.info("%s %s: %d features", name, layer, len(frame))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    target.mkdir(parents=True, exist_ok=True)
    for city in sys.argv[2:] or CITIES:
        download(target, city)
