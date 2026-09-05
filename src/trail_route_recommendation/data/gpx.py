import gpxpy
import geopandas as gpd
from shapely.geometry import LineString
import numpy as np
from typing import IO

def load_gpx(file: IO):
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

def resample_route(route_gdf: gpd.GeoDataFrame, spacing_metres: int = 30) -> gpd.GeoDataFrame:

    # Create route line from GPX points
    line = route_gdf.geometry.iloc[0]

    # Project to a metre-based Coordinate Reference System
    route_metres_projected = gpd.GeoSeries([line], crs=route_gdf.crs).to_crs(
        route_gdf.estimate_utm_crs()
    )

    line_projected = route_metres_projected.iloc[0]

    # Generate distances along the route
    distances = np.arange(0, line_projected.length, spacing_metres)
    distances = np.append(distances, line_projected.length)

    # Create points
    points = [
        line_projected.interpolate(distance)
        for distance in distances
    ]

    # Convert back to original Coordinate Reference System
    resampled_gdf = gpd.GeoDataFrame(
        geometry=points,
        crs=route_metres_projected.crs
    ).to_crs(route_gdf.crs)

    return resampled_gdf