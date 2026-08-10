from src.data.gpx import load_gpx
import geopandas as gpd
from pathlib import Path
from shapely.geometry import box
import requests
from tqdm.auto import tqdm
import rasterio
from rasterio.mask import mask
import numpy as np

def extract_worldcover_features(
    gpx_file_path: str,
    buffer_metres: int = 20,
    worldcover_year: int = 2021):

    route_gdf = load_gpx(gpx_file_path)

    # Extract GPX file bounding box
    geom = box(
        route_gdf.total_bounds[0],
        route_gdf.total_bounds[1],
        route_gdf.total_bounds[2],
        route_gdf.total_bounds[3]
    )

    # Load worldcover grid
    aws_url_prefix = "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
    url = f"{aws_url_prefix}/esa_worldcover_grid.geojson"
    grid = gpd.read_file(url)

    # get grid tiles intersecting box
    tiles = grid[grid.intersects(geom)]

    wc_version = {
        2020: "v100",
        2021: "v200",
    }[worldcover_year]

    output_folder = Path("../data/raw/worldcover")
    output_folder.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(tiles)} WorldCover tiles.")

    for tile in tqdm(tiles.ll_tile):
        download_url = f"{aws_url_prefix}/{wc_version}/{worldcover_year}/map/ESA_WorldCover_10m_{worldcover_year}_{wc_version}_{tile}_Map.tif"
        out_fn = output_folder / Path(download_url).name
        if out_fn.exists():
            print(f"File {out_fn.name} exists already and is skipped")
            continue
        r = requests.get(download_url, allow_redirects=True)
        r.raise_for_status()
        with open(out_fn, "wb") as f:
            f.write(r.content)

    print("All required WorldCover tiles available now.")

    # Project GPX geo DataFrame to metric coordinate system
    gdf_metric = route_gdf.to_crs(route_gdf.estimate_utm_crs())

    # Generate buffer
    buffer_metric = gdf_metric.buffer(buffer_metres)

    # Back to WGS84
    buffer = buffer_metric.to_crs(route_gdf.crs)

    wc_values = np.array([], dtype=np.uint8)

    for tile in tiles.ll_tile:
        wc_tile = output_folder / f"ESA_WorldCover_10m_{worldcover_year}_{wc_version}_{tile}_Map.tif"

        with rasterio.open(wc_tile) as src:
            try:
                masked, out_transform = mask(src, buffer, crop=True)

                values = masked[0]
                values = values[values != 0] # 0 is not an actual worldcover class, but contains pixels that were cropped away due to "crop=True"

                wc_values = np.concatenate((wc_values, values))

            except ValueError as e:
                print(f"Buffer does not overlap this tile: {wc_tile}")
                print(e)
                continue

    classes, counts = np.unique(wc_values, return_counts=True)

    total = counts.sum()

    features = {}

    WORLDCOVER_CLASSES = {
        10: "tree_cover",
        20: "shrubland",
        30: "grassland",
        40: "cropland",
        50: "built_up",
        60: "bare_sparse_vegetation",
        70: "snow_ice",
        80: "permanent_water",
        90: "herbaceous_wetland",
        95: "mangroves",
        100: "moss_lichen",
    }

    for cls, cnt in zip(classes, counts):
        features[WORLDCOVER_CLASSES[int(cls)]] = cnt / total

    return features