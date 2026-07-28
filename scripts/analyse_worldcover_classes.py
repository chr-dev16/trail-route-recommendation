import rasterio
import numpy as np

WORLDCOVER_CLASSES = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / sparse vegetation",
    70: "Snow and ice",
    80: "Permanent water",
    90: "Herbaceous wetland",
    95: "Mangroves",
    100: "Moss and lichen",
}

with rasterio.open("../data/raw/worldcover/ESA_WorldCover_10m_2021_v200_N45E009_Map.tif") as src:
    worldcover = src.read(1)

classes, counts = np.unique(worldcover, return_counts=True)

for cls, cnt in zip(classes, counts):
    print(f"{WORLDCOVER_CLASSES.get(cls, 'Unknown'):25} ({cls}): {cnt:,}")