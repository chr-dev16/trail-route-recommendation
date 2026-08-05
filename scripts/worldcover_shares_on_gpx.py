import gpxpy
from shapely.geometry import LineString
import geopandas as gpd
import matplotlib.pyplot as plt
import rasterio
from rasterio.mask import mask
import numpy as np

with open("../data/raw/gpx/pitz/p105-salomon-ultra-die-koenigsdistanz.gpx", "r") as file:
    gpx = gpxpy.parse(file)

points = []

for track in gpx.tracks:
    for segment in track.segments:
        for point in segment.points:
            points.append((point.longitude, point.latitude))

route = LineString(points)

gdf = gpd.GeoDataFrame(
    {"geometry": [route]},
    crs="EPSG:4326" # same as WorldCover (with longitude and latitude --> in angles)
)

print("Bounding Box:")
print(gdf.total_bounds)

# GPX in metrisches Koordinatensystem projizieren
gdf_utm = gdf.to_crs(gdf.estimate_utm_crs())

# 20-m-Buffer erzeugen
buffer_utm = gdf_utm.buffer(20)

# Back to WGS84
buffer = buffer_utm.to_crs(gdf.crs)

with rasterio.open("../data/raw/worldcover/ESA_WorldCover_10m_2021_v200_N45E009_Map.tif") as wc_tile:
    masked, out_transform = mask(wc_tile, buffer, crop=True)

print(masked.shape)

wc_values = masked[0]

wc_values = wc_values[wc_values != 0] # 0 is not an actual worldcover class, but contains pixels that were cropped away due to "crop=True"

classes, counts = np.unique(wc_values, return_counts=True)

total = counts.sum()

for cls, cnt in zip(classes, counts):
    print(f"{cls}: {cnt:6d} ({100 * cnt / total:.2f} %)")