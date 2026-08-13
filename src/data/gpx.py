import gpxpy
import geopandas as gpd
from shapely.geometry import LineString
from pathlib import Path
import numpy as np

def load_gpx(file_path: Path):
    with open(file_path, "r") as file:
        gpx = gpxpy.parse(file)

    points = []

    for track in gpx.tracks: # usually only 1 track in race GPX files, but not guaranteed
        for segment in track.segments: # usually only 1 track in race GPX files, but not guaranteed
            for point in segment.points:
                points.append((point.longitude, point.latitude))

    route = LineString(points)

    gdf = gpd.GeoDataFrame(
        {"geometry": [route]},
        crs="EPSG:4326" # same as WorldCover (with longitude and latitude --> in angles)
    )
    return gdf

def compute_ascent_descent(gpx_file_path: Path) -> dict[str, float]:
    with open(gpx_file_path, "r") as file:
        gpx = gpxpy.parse(file)

    elevations = []

    for track in gpx.tracks: # usually only 1 track in race GPX files, but not guaranteed
        for segment in track.segments: # usually only 1 track in race GPX files, but not guaranteed
            for point in segment.points:
                if point.elevation is not None:
                    elevations.append(point.elevation)

    elevations = np.array(elevations)

    delta = np.diff(elevations)

    print(delta[:4])

    features = {
        "total_ascent": delta[delta > 0].sum(),
        "total_descent": -delta[delta < 0].sum(),
    }

    return features