from pathlib import Path
from src.data.dem import extract_dem_features
import pandas as pd

GPX_DIR = Path("../data/raw/gpx")
threshold_grid = [7.5]
spacing_metres = 30
results = []

for gpx_file in GPX_DIR.rglob("*.gpx"): # Recursively iterate through all GPX files in the directory
    print(f"Processing {gpx_file}")

    try:
        features = extract_dem_features(gpx_file,
                                        threshold_grid=threshold_grid,
                                        spacing_metres=spacing_metres)
        features["route"] = gpx_file.stem

        results.append(features)
    except Exception as e:
        print(f"Failed: {gpx_file}")
        print(e)
        continue

df = pd.DataFrame(results)

df.to_excel(
    "../data/processed/dem_features.xlsx",
    index=False
)