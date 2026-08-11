from pathlib import Path
from src.data.dem import extract_dem_features
import pandas as pd

GPX_DIR = Path("../data/raw/gpx")
results = []

for gpx_file in GPX_DIR.rglob("*.gpx"): # Recursively iterate through all GPX files in the directory
    print(f"Processing {gpx_file}")

    try:
        features = extract_dem_features(gpx_file)
        features["route"] = gpx_file.stem

        results.append(features)
    except Exception as e:
        print(f"Failed: {gpx_file}")
        print(e)
        continue

df = pd.DataFrame(results)

df.to_csv(
    "../data/processed/dem_features.csv",
    index=False
)