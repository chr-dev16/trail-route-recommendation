import gpxpy
from shapely.geometry import LineString
import geopandas as gpd

with open("../data/raw/gpx/IATF26_K25_260429.gpx", "r") as file:
    gpx = gpxpy.parse(file)

points = []

for track in gpx.tracks:
    for segment in track.segments:
        for point in segment.points:
            points.append((point.longitude, point.latitude))

route = LineString(points)

gdf = gpd.GeoDataFrame(
    geometry=route,
    crs="EPSG:4326" # same as WorldCover
)

print(gdf)
print(gdf.total_bounds)