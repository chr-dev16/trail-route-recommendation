from src.data.gpx import load_gpx
import geopandas as gpd
from pathlib import Path
import rasterio
import numpy as np

def extract_elevations(route_gdf: gpd.GeoDataFrame, dem: rasterio.io.DatasetReader) -> np.ndarray:

    coordinates = list(route_gdf.geometry.iloc[0].coords)

    elevations = []

    for value in dem.sample(coordinates):
        elevations.append(value[0])

    return np.array(elevations)

def compute_elevation_features(elevations: np.ndarray) -> dict[str, int]:

    delta = np.diff(elevations)

    features = {
        "min_elevation": np.min(elevations),
        "max_elevation": np.max(elevations),
        "mean_elevation": np.mean(elevations),
        "std_elevation": np.std(elevations),
        "elevation_range": np.max(elevations) - np.min(elevations),
        "total_ascent": delta[delta > 0].sum(),
        "total_descent": -delta[delta < 0].sum(),
    }

    return features

def extract_dem_features(gpx_file_path: Path,
                         dem_path: Path = Path("../data/raw/gedtm30/gedtm30_alps.tif")
                         ) -> dict[str, int]:

    route_gdf = load_gpx(gpx_file_path)

    with rasterio.open(dem_path) as dem:
        elevations = extract_elevations(route_gdf, dem)
    
    features = compute_elevation_features(elevations)

    return features