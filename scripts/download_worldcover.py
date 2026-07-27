# from https://esa-worldcover.org/en/data-access

import geopandas as gpd
from pathlib import Path
from shapely.geometry import box
import requests
from tqdm.auto import tqdm

YEAR = 2021

aws_url_prefix = "https://esa-worldcover.s3.eu-central-1.amazonaws.com"

# load worldcover grid
url = f"{aws_url_prefix}/esa_worldcover_grid.geojson"
grid = gpd.read_file(url)

geom = box(
    10.0, # X_min
    46.0, # Y_min
    10.5, # X_max
    46.5, # Y_max
)

# get grid tiles intersecting box
tiles = grid[grid.intersects(geom)]

version = {
    2020: "v100",
    2021: "v200",
}[YEAR]

output_folder = Path("../data/raw/worldcover")
output_folder.mkdir(parents=True, exist_ok=True)

print(f"Found {len(tiles)} WorldCover tiles.")

for tile in tqdm(tiles.ll_tile):
    download_url = f"{aws_url_prefix}/{version}/{YEAR}/map/ESA_WorldCover_10m_{YEAR}_{version}_{tile}_Map.tif"
    r = requests.get(download_url, allow_redirects=True)
    r.raise_for_status()
    out_fn = output_folder / Path(download_url).name
    if out_fn.exists():
        printf("File {out_fn.name} exists already and is skipped")
        continue
    with open(out_fn, "wb") as f:
        f.write(r.content)

print("Download completed.")