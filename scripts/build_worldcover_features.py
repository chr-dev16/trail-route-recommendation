from pathlib import Path
from src.data.worldcover import extract_worldcover_features
import pandas as pd

GPX_DIR = Path("../data/raw/gpx")
results = []

for gpx_file in GPX_DIR.rglob("*.gpx"): # Recursively iterate through all GPX files in the directory
    features = extract_worldcover_features(gpx_file)
    features["race"] = gpx_file.stem

df = pd.DataFrame(results)

df.to_csv(
    "../data/processed/worldcover_features.csv",
    index=False
)