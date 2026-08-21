from src.data.gpx import load_gpx, resample_route
import geopandas as gpd
from pathlib import Path
import rasterio
import numpy as np

def extract_dtm_raster_values(route_gdf: gpd.GeoDataFrame, dtm: rasterio.io.DatasetReader) -> np.ndarray:

    route_dtm_crs = route_gdf.to_crs(dtm.crs)

    coordinates = []

    for point in route_dtm_crs.geometry:
        coordinates.append((point.x, point.y))

    values = []

    for value in dtm.sample(coordinates):
        values.append(value[0])

    return np.array(values)

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

        "fraction_uphill": np.mean(slope_percent > 0),
        "fraction_uphill_above_10percent": np.mean(slope_percent > 10),
        "fraction_uphill_above_20percent": np.mean(slope_percent > 20),
        "fraction_uphill_above_30percent": np.mean(slope_percent > 30),

        "fraction_downhill": np.mean(slope_percent < 0),
        "fraction_downhill_above_10percent": np.mean(slope_percent < -10),
        "fraction_downhill_above_20percent": np.mean(slope_percent < -20),
        "fraction_downhill_above_30percent": np.mean(slope_percent < -30),
    }

    return slope_features

def compute_climb_features(
    elevations: np.ndarray,
    spacing_metres: float = 30,
    slope_window: int = 5,
    min_climb_length: float = 300) -> dict[str, float]:

    # Calculate local slope over the 150-m window
    delta = elevations[slope_window:] - elevations[:-slope_window]

    slope_percent = (
        delta / (spacing_metres * slope_window)
    ) * 100

    # Identify uphill sections (bool per section)
    uphill = slope_percent > 0

    # Find continuous uphill sections
    climbs = []

    start = None

    for i, is_uphill in enumerate(uphill):

        if is_uphill and start is None:
            start = i

        elif not is_uphill and start is not None:
            end = i

            length = (end - start) * spacing_metres

            if length >= min_climb_length:
                elevation_gain = (
                    elevations[end - 1 + slope_window]
                    - elevations[start]
                )

                climbs.append({
                    "length": length,
                    "elevation_gain": elevation_gain
                })

            start = None

    if start is not None:

        end = len(uphill)

        length = (end - start) * spacing_metres

        if length >= min_climb_length:
            elevation_gain = (
                elevations[end - 1 + slope_window]
                - elevations[start]
            )

            climbs.append({
                "length": length,
                "elevation_gain": elevation_gain
            })

    if climbs:
        lengths = np.array([climb["length"] for climb in climbs])
        gains = np.array([climb["elevation_gain"] for climb in climbs])

        return {
            "number_of_climbs": len(climbs),
            "total_climb_distance": lengths.sum(),
            "mean_climb_length": lengths.mean(),
            "max_climb_length": lengths.max(),
            "mean_climb_elevation_gain": gains.mean(),
            "max_climb_elevation_gain": gains.max(),
        }
    else:
        return {
            "number_of_climbs": 0,
            "total_climb_distance": 0,
            "mean_climb_length": 0,
            "max_climb_length": 0,
            "mean_climb_elevation_gain": 0,
            "max_climb_elevation_gain": 0,
        }

def compute_ssdon_features(
    ssdon_values: np.ndarray
) -> dict[str, float]:

    return {
        "mean_ssdon": np.mean(ssdon_values),
        "p95_ssdon": np.percentile(ssdon_values, 95),
    }

def compute_openness_features(
    openness_values: np.ndarray,
    reference: str,
) -> dict[str, float]:

    return {
        f"mean_{reference}_openness": np.mean(openness_values),
        f"p95_{reference}_openness": np.percentile(openness_values, 95),
    }
    

def extract_dem_features(gpx_file_path: Path,
                         threshold_grid: list,
                         spacing_metres: float = 30,
                         dem_path: Path = Path("../data/raw/gedtm30/gedtm30_alps.tif"),
                         ssdon_path: Path = Path("../data/raw/gedtm30/ssdon_gedtm30_alps.tif"),
                         positive_openness_path: Path = Path("../data/raw/gedtm30/positive_openness_gedtm30_alps.tif"),
                         negative_openness_path: Path = Path("../data/raw/gedtm30/positive_openness_gedtm30_alps.tif"),
                         ) -> dict[str, int]:

    route_gdf = load_gpx(gpx_file_path)

    # Resample GPX route to regularly spaced points
    resampled_gdf = resample_route(route_gdf, spacing_metres=spacing_metres)

    with rasterio.open(dem_path) as dem, rasterio.open(ssdon_path) as ssdon, rasterio.open(positive_openness_path) as pos_openness, rasterio.open(negative_openness_path) as neg_openness:
        elevations = extract_dtm_raster_values(resampled_gdf, dem)
        ssdon_values = extract_dtm_raster_values(resampled_gdf, ssdon)
        pos_openness_values = extract_dtm_raster_values(resampled_gdf, pos_openness)
        neg_openness_values = extract_dtm_raster_values(resampled_gdf, neg_openness)

    features = compute_elevation_features(elevations, threshold_grid)
    features.update(compute_slope_features(elevations, spacing_metres=spacing_metres))
    features.update(compute_climb_features(elevations))
    features.update(compute_ssdon_features(ssdon_values))
    features.update(compute_openness_features(pos_openness_values, "positive"))
    features.update(compute_openness_features(neg_openness_values, "negative"))

    return features