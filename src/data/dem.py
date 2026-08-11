from src.data.gpx import load_gpx
from shapely.geometry import box

def extract_dem_features(
    gpx_file_path: str):

    route_gdf = load_gpx(gpx_file_path)

    # Extract GPX file bounding box
    geom = box(
        route_gdf.total_bounds[0],
        route_gdf.total_bounds[1],
        route_gdf.total_bounds[2],
        route_gdf.total_bounds[3]
    )