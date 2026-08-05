from pathlib import Path
from src.data.worldcover import extract_worldcover_features

GPX_DIR = Path("../data/raw/gpx")

for gpx_file in GPX_DIR.rglob("*.gpx"): # Recursively iterate through all GPX files in the directory
    features: dict[str, float] = extract_worldcover_features(gpx_file)