import gpxpy
import geopandas as gpd
from shapely.geometry import LineString
from pathlib import Path

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