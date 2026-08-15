from src.data.gpx import load_gpx, resample_route
import geopandas as gpd
from pathlib import Path
import rasterio
import numpy as np

def extract_elevations(route_gdf: gpd.GeoDataFrame, dem: rasterio.io.DatasetReader) -> np.ndarray:

    route_dem_crs = route_gdf.to_crs(dem.crs)

    coordinates = []

    for point in route_dem_crs.geometry:
        coordinates.append((point.x, point.y))

    elevations = []

    for value in dem.sample(coordinates):
        elevations.append(value[0])

    return np.array(elevations)

def compute_elevation_features(elevations: np.ndarray,
                               threshold_grid: list) -> dict[str, float]:

    delta = np.diff(elevations)

    elevation_features = {
        "min_elevation": np.min(elevations),
        "max_elevation": np.max(elevations),
        "mean_elevation": np.mean(elevations),
        "std_elevation": np.std(elevations),
        "elevation_range": np.max(elevations) - np.min(elevations),
    }

    for threshold in threshold_grid:
        elevation_features[f"total_ascent_{threshold}"] = delta[delta > threshold].sum()

        elevation_features[f"total_descent_{threshold}"] = -delta[delta < -threshold].sum()

    return elevation_features

def compute_slope_features(elevations: np.ndarray, 
                           spacing_metres: float = 30,
                           slope_window: int = 5) -> dict[str, float]:

    delta = elevations[slope_window:] - elevations[:-slope_window]

    slope_percent = (delta / (spacing_metres * slope_window)) * 100

    distance_km = np.arange(slope_window, len(elevations)) * spacing_metres / 1000

    uphill_slope = slope_percent[slope_percent > 0]
    downhill_slope = slope_percent[slope_percent < 0]

    slope_features = {
        "mean_uphill_slope": uphill_slope.mean(),
        "mean_downhill_slope": -downhill_slope.mean(),
        "p95_uphill_slope": np.percentile(uphill_slope, 95),
        "p95_downhill_slope": -np.percentile(downhill_slope, 5),

        "fraction_uphill_above_10percent": np.mean(slope_percent > 10),
        "fraction_uphill_above_20percent": np.mean(slope_percent > 20),
        "fraction_uphill_above_30percent": np.mean(slope_percent > 30),

        "fraction_downhill_above_10percent": np.mean(slope_percent < -10),
        "fraction_downhill_above_20percent": np.mean(slope_percent < -20),
        "fraction_downhill_above_30percent": np.mean(slope_percent < -30),
    }

    return slope_features

def extract_dem_features(gpx_file_path: Path,
                         threshold_grid: list,
                         spacing_metres: float = 30,
                         dem_path: Path = Path("../data/raw/gedtm30/gedtm30_alps.tif"),
                         ) -> dict[str, int]:

    route_gdf = load_gpx(gpx_file_path)

    # Resample GPX route to regularly spaced points
    resampled_gdf = resample_route(route_gdf, spacing_metres=spacing_metres)

    with rasterio.open(dem_path) as dem:
        elevations = extract_elevations(resampled_gdf, dem)

    features = compute_elevation_features(elevations, threshold_grid)
    features.update(compute_slope_features(elevations, spacing_metres=spacing_metres))

    return features