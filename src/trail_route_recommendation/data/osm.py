import geopandas as gpd
import osmnx as ox
import ast

SAC_SCALE_CODES = {
    "hiking": "T1",
    "mountain_hiking": "T2",
    "demanding_mountain_hiking": "T3",
    "alpine_hiking": "T4",
    "demanding_alpine_hiking": "T5",
    "difficult_alpine_hiking": "T6",
}


def format_sac_scale(value: str) -> str:
    # Account for multiple comma-separated tag value on OSM edges
    try:
        values = ast.literal_eval(value) if value.startswith("[") else [value]
    except (ValueError, SyntaxError):
        values = [value]

    display_values = []

    for item in values:
        display_values.append(f"{item} ({SAC_SCALE_CODES.get(item, item)})")

    if len(display_values) > 1:
        return f"[{', '.join(display_values)}]"
    else:
        return display_values[0]


def match_gpx_to_edges(
    gpx: gpd.GeoDataFrame,
    graph,
    bad_match_threshold_m = 100,
) -> gpd.GeoDataFrame:

    # Project GPX points and graph to a suitable metric CRS
    target_crs = gpx.estimate_utm_crs()

    gpx_projected = gpx.to_crs(target_crs)
    graph_projected = ox.project_graph(graph, to_crs=target_crs)

    # Get GPX point coordinates
    x = gpx_projected.geometry.x.to_numpy()
    y = gpx_projected.geometry.y.to_numpy()

    # Find nearest OSM graph edge for each GPX point
    # take note that matched_edge_ids is a NDArray (because in osmnx edges are identified via (u, v, key))
    # --> Needs to be converted to a Python tuple later to be hashable for addition into a set
    matched_edge_ids = ox.distance.nearest_edges(
        graph_projected,
        X=x,
        Y=y,
    )

    # Convert graph edges to GeoDataFrame
    edges = ox.graph_to_gdfs(
        graph_projected,
        nodes=False,
        edges=True,
    )

    # Keep only matches within 100 m
    valid_edge_ids = set()

    for point, edge_id in zip(gpx_projected.geometry, matched_edge_ids):
        edge_geometry = edges.loc[tuple(edge_id)].geometry

        distance = point.distance(edge_geometry)

        if distance <= bad_match_threshold_m:
            valid_edge_ids.add(tuple(edge_id))

    # Select valid matched edges out of the GeoDataFrame
    matched_edges = edges.loc[
        edges.index.isin(valid_edge_ids)
    ].copy()

    return matched_edges