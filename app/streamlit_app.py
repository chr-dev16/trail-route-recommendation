import streamlit as st
from pathlib import Path
import json
import tempfile
import gpxpy.gpx
from shapely.ops import substring
import pandas as pd
import plotly.graph_objects as go
import osmnx as ox
import networkx as nx
import geopandas as gpd
import rasterio
import numpy as np
from shapely.geometry import Point
from trail_route_recommendation.data.gpx import resample_route
from trail_route_recommendation.data.osm_pruning_debugging import format_sac_scale
from trail_route_recommendation.data.worldcover import extract_worldcover_features
from trail_route_recommendation.data.dem import extract_dem_features
from trail_route_recommendation.routing.route_generator import similarity_error, osm_features, generate_routes, route_to_gpx
from ui.settings import OSM_TAGS
from ui.data_loading import load_uploaded_route, load_uploaded_graph, get_matched_edges
from ui.route_display import add_gpx, add_graph, add_matched_edges, render_map

# Hyperparameter setting. 7.5 m showed smallest error to official race ascent/descent values
FEATURE_OPTIONS = {
    "dtm": {"threshold_grid": [7.5], "spacing_metres": 30},
    "worldcover": {"buffer_metres": 20, "worldcover_year": 2021},
}
FEATURE_SCALES_PATH = Path("./data/processed/feature_scales.json")


def load_feature_scales(): # Needed for feature normalisation
    if not FEATURE_SCALES_PATH.exists():
        raise FileNotFoundError("Feature scales not found. Run build_feature_scales.py first.")
    data = json.loads(FEATURE_SCALES_PATH.read_text(encoding="utf-8"))
    return data["scales"]


def get_osm_features(route, points, graph):
    metric_crs = route.estimate_utm_crs()

     # Download OSM graph around route if no graph was provided
    if graph is None:
        for tag in ["highway", "surface", "sac_scale", "tracktype"]:
            if tag not in ox.settings.useful_tags_way:
                ox.settings.useful_tags_way.append(tag)

        # Create 500 m buffer around route in metric CRS and convert back to latitude/longitude for OSM graph download
        polygon = route.to_crs(metric_crs).geometry.buffer(500).to_crs("EPSG:4326").iloc[0]
        graph = ox.graph_from_polygon(polygon, network_type="all", simplify=True, retain_all=True)

    # Make sure that OSM graph and GPX points are both in metric CRS such that 100 m matching threshold can work later
    graph = ox.project_graph(graph, to_crs=metric_crs)
    points = points.to_crs(metric_crs)
    xs = []
    ys = []
    lengths = []

    # use midpoint and length of each pair of consecutive GPX points for matching to OSM edges
    for index in range(len(points) - 1):
        first = points.geometry.iloc[index]
        next = points.geometry.iloc[index + 1]
        xs.append((first.x + next.x) / 2) # calculate midpoint
        ys.append((first.y + next.y) / 2) # calculate midpoint
        lengths.append(first.distance(next))

    edge_ids, distances = ox.distance.nearest_edges(graph, X=xs, Y=ys, return_dist=True) # Get OSM edges that are the closest to GPX points
    edge_data = []
    for edge_id, distance in zip(edge_ids, distances):
        if distance <= 100: # matching threshold of 100 m
            edge_data.append(graph.edges[edge_id])
        else:
            edge_data.append({})
    return osm_features(edge_data, lengths)

# Cache extracted features to avoid repeating the expensive feature extraction when inputs have not changed. Limit to 10 entries to avoid that cache grows endlessly when app is deployed and many different routes are uploaded
@st.cache_data(max_entries=10)
def extract_comparison_features(gpx_bytes, graphml_bytes, gpx_name, route_number):
    # Store uploaded GPX temporarily because DTM and WorldCover feature extraction expect a file path
    cache_folder = Path("./data/uploads")
    cache_folder.mkdir(parents=True, exist_ok=True)
    gpx_path = cache_folder / ("comparison_route_" + str(route_number) + "_" + Path(gpx_name).name)

    # Only write GPX file if it does not exist yet or uploaded data has changed
    if not gpx_path.exists() or gpx_path.read_bytes() != gpx_bytes:
        gpx_path.write_bytes(gpx_bytes)

    # 1. Collect DTM features
    route = load_uploaded_route(gpx_bytes)
    points = resample_route(route, spacing_metres=30) # resampling with 30 m intervals
    features = extract_dem_features(gpx_path, **FEATURE_OPTIONS["dtm"])

    # 2. Collect WorldCover features
    worldcover = extract_worldcover_features(gpx_path, **FEATURE_OPTIONS["worldcover"])
    class_names = [
        "tree_cover", "shrubland", "grassland", "cropland", "built_up",
        "bare_sparse_vegetation", "snow_ice", "permanent_water",
        "herbaceous_wetland", "mangroves", "moss_lichen",
    ]
    if not worldcover: # Missing raster data should not look like a route with zero shares
        raise ValueError("No WorldCover data found for the reference or comparison route.")
    for name in class_names:
        features[name] = worldcover.get(name, 0.0)

    # 3. Collect distance feature
    metric_route = route.to_crs(route.estimate_utm_crs())
    features["distance_m"] = float(metric_route.length.sum()) # Add length of calculated route

    # 4. Collect OSM features
    graph = None
    if graphml_bytes is not None:
        graph = load_uploaded_graph(graphml_bytes) # Conversion of bytes object to MultziDiGraph
    features.update(get_osm_features(route, points, graph))

    return features


def get_group_error(candidate, reference, scales, group):
    group_weights = {"distance": 0.0, "dem": 0.0, "worldcover": 0.0, "osm": 0.0}
    group_weights[group] = 1.0 # Only activate the group that was passed to the function
    return similarity_error(candidate, reference, scales=scales, group_weights=group_weights)


def show_feature_comparison(route_1, route_2):
    """Display similarity scoring in Streamlit app"""
    with st.spinner("Calculating route features..."):
        # get all individual features
        reference = extract_comparison_features(
            route_1["gpx_bytes"], route_1["graphml_bytes"], route_1["gpx_name"], 1,
        )
        candidate = route_2.get("features") # Generated routes have already been measured
        if candidate is None:
            candidate = extract_comparison_features(
                route_2["gpx_bytes"], route_2["graphml_bytes"], route_2["gpx_name"], 2,
            )

    scales = load_feature_scales()
    overall_error = similarity_error(
        candidate,
        reference,
        scales=scales, # Normalisation is happening here
        group_weights={"distance": 0.0, "dem": 1.0, "worldcover": 1.0, "osm": 1.0}, # Overall similarity error is calculated by equal feature group weighting
    )

    # get group errors for displaying
    dtm_error = get_group_error(candidate, reference, scales, "dem")
    worldcover_error = get_group_error(candidate, reference, scales, "worldcover")
    osm_error = get_group_error(candidate, reference, scales, "osm")

    st.subheader("Feature comparison")
    st.metric("Overall feature error (lower is better)", round(overall_error, 4))
    st.caption(
        "Feature error by group: DTM: " + str(round(dtm_error, 4))
        + " | WorldCover: " + str(round(worldcover_error, 4))
        + " | OSM: " + str(round(osm_error, 4))
    )
    st.caption("Selected reference versus Route 2; DTM, WorldCover and OSM weighted equally. Distance is shown but not scored.")

    rows = []
    for feature, value in reference.items():
        rows.append({
            "Feature": feature,
            "Route 1 (reference)": value,
            "Route 2": candidate.get(feature),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch") # Comparison table



# Keep downloaded graphs for later use.
@st.cache_resource(max_entries=3)
def load_generation_graph(latitude, longitude, half_width, end_latitude=None, end_longitude=None):
    # Keep these tags during subsequent OSM download for the feature comparison
    for tag in ["highway", "surface", "sac_scale", "tracktype"]:
        if tag not in ox.settings.useful_tags_way:
            ox.settings.useful_tags_way.append(tag)
    # OSM graph is downloaded from Overpass API. Converting kilometres to metres for the download area.
    if end_latitude is None or end_longitude is None: # Case 1: Automatic finish
        graph = ox.graph_from_point((latitude, longitude), dist=half_width * 1000,
                                   dist_type="bbox", network_type="walk")
    else: # Cases 2 & 3: Round trip and user-specified finish
        # Include the area around both start and finish
        start_bounds = ox.utils_geo.bbox_from_point((latitude, longitude), dist=half_width * 1000)
        end_bounds = ox.utils_geo.bbox_from_point((end_latitude, end_longitude), dist=half_width * 1000)
        bounds = (min(start_bounds[0], end_bounds[0]), min(start_bounds[1], end_bounds[1]),
                  max(start_bounds[2], end_bounds[2]), max(start_bounds[3], end_bounds[3]))
        graph = ox.graph_from_bbox(bounds, network_type="walk", retain_all=True)
    # Save the graph to then read it as file contents (bytes)
    with tempfile.TemporaryDirectory() as folder:
        graph_path = Path(folder) / "generated_area.graphml"
        ox.save_graphml(graph, filepath=graph_path)
        graph_bytes = graph_path.read_bytes()
    return graph, graph_bytes


def get_finish_heights_from_dem(points, dem):
    points = points.to_crs(dem.crs) # The finish points need the same coordinate reference system as the DTM raster
    coordinates = []
    for point in points.geometry:
        coordinates.append((point.x, point.y))
    heights = []
    for coordinate, height in zip(coordinates, dem.sample(coordinates, masked=True)): # Query the DTM
        x, y = coordinate
        is_outside_dem_bounds = not (dem.bounds.left <= x < dem.bounds.right and dem.bounds.bottom < y <= dem.bounds.top)
        if is_outside_dem_bounds or np.ma.is_masked(height[0]) or not np.isfinite(height[0]):
            heights.append(float("nan")) # Missing data must not look like sea level
        else:
            heights.append(float(height[0]))
    return heights


def get_finish_height_error(candidate): # For later use as sorting key
    return candidate["height_error"]


def choose_automatic_finishes(graph, start, maximum_distance, reference_route, dem_path):
    # Only consider nodes we can reach within the maximum route length
    distances = nx.single_source_dijkstra_path_length(graph, start, cutoff=maximum_distance, weight="length") # Calculate shortest paths between start node and all reachable nodes using Dijsktra
    node_ids = sorted(distances) # Keep the same node order each time
    nodes = ox.graph_to_gdfs(graph.subgraph(node_ids), nodes=True, edges=False) # Subgraph only contains reachable nodes

    # Get the first and last point of the reference route (to calculate height difference)
    coordinates = list(reference_route.geometry.iloc[0].coords)
    reference_ends = gpd.GeoDataFrame(geometry=[Point(coordinates[0]), Point(coordinates[-1])], crs=reference_route.crs)
    with rasterio.open(dem_path) as dem:
        reference_heights = get_finish_heights_from_dem(reference_ends, dem)
        node_heights = get_finish_heights_from_dem(nodes, dem) # Read all reachable node heights in one pass

    # Stop if the reference endpoint heights are missing
    if not np.isfinite(reference_heights).all():
        raise ValueError("The reference start and/or finish are outside valid DTM coverage.")
    target_change = reference_heights[1] - reference_heights[0] # Height at finish minus height at start

    # Store each height under its node ID so we can look it up later
    heights = {}
    for node_id, height in zip(nodes.index, node_heights):
        heights[node_id] = height
    if not np.isfinite(heights[start]):
        raise ValueError("The chosen start is outside valid DTM coverage.")

    candidates = []
    for node_id in node_ids:
        # Skip the start, zero-length connections and missing heights
        if node_id == start or distances[node_id] <= 0 or not np.isfinite(heights[node_id]):
            continue
        change = heights[node_id] - heights[start]
        # Smaller error means the height change is closer to the reference
        candidates.append({"node": node_id, "height_error": abs(change - target_change)})
    candidates.sort(key=get_finish_height_error) # Best height match comes first
    if not candidates:
        raise ValueError("No reachable finish with valid elevation data found.")

    # Keep a height match first, then try other places and heights as well
    metric_nodes = nodes.to_crs(nodes.estimate_utm_crs()) # Measure separation in metres
    positions = {} # Save spatial position of each node to facilitate spatial diversity later
    valid_heights = []
    for candidate in candidates:
        node_id = candidate["node"]
        positions[node_id] = metric_nodes.loc[node_id].geometry
        valid_heights.append(heights[node_id])

    # Scale height differences; use at least 100 m even in a fairly flat area
    height_scale = max(100.0, max(valid_heights) - min(valid_heights))
    finishes = [candidates[0]["node"]] # Start with the best reference-height match

    while len(finishes) < 5:
        best_node, best_score = None, -1.0
        for candidate in candidates:
            node_id = candidate["node"]
            if node_id in finishes:
                continue # This finish is already selected
            diversity_score = float("inf")

            # Compare this candidate with every finish selected so far
            for finish in finishes:
                separation = positions[node_id].distance(positions[finish]) # Distance in meters
                if separation < 500:
                    diversity_score = -1.0 # Do not fill all places with nearby nodes (spatial diversity!)
                    break
                # Combine distance apart and height difference on comparable scales
                difference = separation / maximum_distance + abs(heights[node_id] - heights[finish]) / height_scale
                diversity_score = min(diversity_score, difference) # Keep the smallest difference to any selected finish (case of least spatial diversity) --> Larger values of diversity_score are better

            # Prefer a candidate that differs from all previously selected finishes
            if diversity_score > best_score:
                best_node, best_score = node_id, diversity_score
        if best_node is None:
            break # No more candidates meet the spacing rule. Less than five finishes might be selected
        finishes.append(best_node)
    return finishes # Can contain fewer than five nodes


def select_generated_route(reference):
    if reference is None:
        st.info("Select Route 1 before generating a route.")
        return None
    reference_route = load_uploaded_route(reference["gpx_bytes"])
    # Reference distance in metres is calculated in metric CRS
    reference_distance = reference_route.to_crs(reference_route.estimate_utm_crs()).length.sum()

    # Most generation parameters are specified by the user in the sidebar
    latitude = st.sidebar.number_input("Start latitude", min_value=-89.0, max_value=89.0,
                                       value=47.40881, format="%.5f")
    longitude = st.sidebar.number_input("Start longitude", min_value=-180.0, max_value=180.0,
                                        value=10.29202, format="%.5f")
    finish_mode = st.sidebar.selectbox("Finish", ["Round trip", "Choose coordinates", "Automatic finish"])
    end_latitude, end_longitude = None, None # A round trip ends at the start
    if finish_mode == "Automatic finish":
        st.sidebar.caption("Compare reachable finishes using reference height change and different locations.")
    if finish_mode == "Choose coordinates":
        end_latitude = st.sidebar.number_input("Finish latitude", min_value=-89.0, max_value=89.0,
                                               value=47.40881, format="%.5f")
        end_longitude = st.sidebar.number_input("Finish longitude", min_value=-180.0, max_value=180.0,
                                                value=10.29202, format="%.5f")
    half_width = st.sidebar.number_input("Search half-width (km)", min_value=1.0, value=10.0)
    # Default is 80% - 120% of distance of reference route, but user can change it
    minimum = st.sidebar.number_input("Minimum distance (km)", min_value=0.1,
                                      value=max(0.1, round(reference_distance / 1000 * 0.8, 2)), step=0.1)
    maximum = st.sidebar.number_input("Maximum distance (km)", min_value=0.1,
                                      value=max(0.1, round(reference_distance / 1000 * 1.2, 2)), step=0.1)
    attempts = st.sidebar.number_input("Search attempts", min_value=1, value=100, step=10)
    seed = st.sidebar.number_input("Random seed", min_value=0, value=42, step=1)
    dem_weight = st.sidebar.number_input("DTM weight", min_value=0.0, value=1.0, step=0.5)
    wc_weight = st.sidebar.number_input("WorldCover weight", min_value=0.0, value=1.0, step=0.5)
    osm_weight = st.sidebar.number_input("OSM weight", min_value=0.0, value=1.0, step=0.5)
    # Distance only sets the allowed range now; the other features choose the best routes
    groups = {"distance": 0.0, "dem": dem_weight, "worldcover": wc_weight, "osm": osm_weight}
    if dem_weight + wc_weight + osm_weight == 0:
        st.warning("Select at least one positive feature weight.")
        return None
    st.sidebar.caption(finish_mode + ": " + str(minimum) + "-" + str(maximum)
                       + " km. Search uses DTM, WorldCover and OSM similarity, with a penalty for edge repetition above 10%.")
    if minimum > maximum: # Stop here if the range does not make sense
        st.warning("Minimum distance must not exceed maximum distance.")
        return None
    # Saving the settings which were used for these routes
    settings = (reference["gpx_bytes"], latitude, longitude, half_width, attempts, seed, minimum, maximum,
                finish_mode, end_latitude, end_longitude, reference["graphml_bytes"],
                dem_weight, wc_weight, osm_weight) # Changed weights also need a new search
    if st.sidebar.button("Generate routes"): # Below runs once user starts route generation
        st.session_state.pop("generated_result", None)  # Old results are cleared before re-start
        try:
            with st.spinner("Loading the OSM network and searching for routes..."):
                # Use the same reference features and scales as in the comparison below
                reference_features = extract_comparison_features(
                    reference["gpx_bytes"], reference["graphml_bytes"], reference["gpx_name"], 1)
                scales = load_feature_scales()
                options = {"dem": FEATURE_OPTIONS["dtm"], "worldcover": FEATURE_OPTIONS["worldcover"], "osm": {}}
                similarity_error(reference_features, reference_features, scales=scales, group_weights=groups)
                graph, graph_bytes = load_generation_graph(latitude, longitude, half_width,
                                                            end_latitude, end_longitude)
                # Find the nearest point in the OSM network.
                start_node = ox.distance.nearest_nodes(graph, X=longitude, Y=latitude)
                end_node = start_node
                finishes = None
                if finish_mode == "Automatic finish":
                    dem_path = FEATURE_OPTIONS["dtm"].get("dem_path", Path("../data/raw/gedtm30/gedtm30_alps.tif"))
                    finishes = choose_automatic_finishes(graph, start_node, maximum * 1000, reference_route, dem_path)
                if finish_mode == "Choose coordinates":
                    end_node = ox.distance.nearest_nodes(graph, X=end_longitude, Y=end_latitude)
                    if end_node == start_node: # Both coordinates ended up at the same OSM node
                        raise ValueError("Start and finish are too close. Choose another finish or use Round trip.")
                # Compare valid routes by their features before choosing the best three
                candidates = generate_routes(graph, start_node, reference_features,
                                             attempts=attempts, seed=seed, end=end_node, end_nodes=finishes,
                                             feature_options=options, scales=scales, group_weights=groups,
                                             distance_range=(minimum * 1000, maximum * 1000)) # Convert km to metres
                # Prepare a GPX file for each route.
                gpx_files = []
                for candidate in candidates:
                    gpx_files.append(route_to_gpx(graph, candidate["nodes"]))
                    marker_nodes = [candidate["nodes"][0]] + candidate["waypoints"] + [candidate["nodes"][-1]]
                    markers = []
                    for node in marker_nodes:
                        markers.append((graph.nodes[node]["x"], graph.nodes[node]["y"]))
                    candidate["markers"] = markers # Save map positions while the graph is available
                # Keep the results when the user changes the displayed candidate.
                st.session_state["generated_result"] = {
                    "settings": settings, "gpx_files": gpx_files, "graph_bytes": graph_bytes,
                    "candidates": candidates, # Keep the scores for the selected route as well
                }
        except Exception as error:
            st.error("Could not generate routes: " + str(error))
    result = st.session_state.get("generated_result")
    if result is None:
        return None
    # User is asked for a new search if settings have changed. Route generation is stopped
    if result["settings"] != settings:
        st.info("Settings changed. Click Generate routes to update the results.")
        return None
    if not result["gpx_files"]:
        st.warning("No route found within the distance range. Try more attempts or another seed.")
        return None
    
    route_choice = []   # Create names for the route dropdown
    for index in range(len(result["gpx_files"])):
        route_choice.append("Candidate " + str(index + 1))
    selected = st.selectbox("Generated route", route_choice) # Collect clicked race in drpodown
    index = route_choice.index(selected)
    candidate = result["candidates"][index]
    st.caption("Weighted feature error: " + str(round(candidate["error"], 4))
                + " | Repeated distance: " + str(round(candidate["repeat_fraction"] * 100, 1)) + "%"
                + " | Penalty: " + str(round(candidate["repeat_penalty"], 4))
                + " | Search score: " + str(round(candidate["total_error"], 4)))
    st.caption("Search uses your weights; the comparison below keeps equal group weights.")
    name = "generated_route_" + str(index + 1) + ".gpx"
    gpx_bytes = result["gpx_files"][index]
    st.download_button("Download generated GPX", gpx_bytes, file_name=name,
                       mime="application/gpx+xml")
    return {"gpx_bytes": gpx_bytes, "graphml_bytes": result["graph_bytes"], "gpx_name": name,
            "markers": candidate["markers"], "features": candidate["features"]} # Reuse the features below


def select_reference_segment(route, gpx_bytes, gpx_name):
    metric_route = route.to_crs(route.estimate_utm_crs())
    line = metric_route.geometry.iloc[0]
    total_km = float(line.length) / 1000
    if total_km <= 0:
        st.warning("The reference route has no length.")
        return None
    # Reset the selection when another GPX file is loaded
    if st.session_state.get("segment_source") != gpx_bytes:
        st.session_state.pop("reference_segment", None)
        st.session_state["segment_source"] = gpx_bytes  # Write the new GPX bytes as base for fresh segment selection
    segment_start, segment_end = st.sidebar.slider("Reference segment (km)", min_value=0.0, max_value=total_km,
                                    value=(0.0, total_km), step=min(0.1, total_km),
                                    format="%.2f", key="reference_segment")
    if segment_start >= segment_end:
        st.warning("Choose a reference segment with different start and end distances.")
        return None
    st.sidebar.caption("Selected reference: " + str(round(segment_end - segment_start, 2)) + " km")
    if segment_start == 0 and segment_end == total_km:
        return gpx_bytes, gpx_name, route # Keep the original file when everything is selected

    # Cut along the route, keeping bends and the original running direction
    segment_route = metric_route.copy()
    segment_route.geometry = [substring(line, segment_start * 1000, segment_end * 1000)] # Carving out the segment
    segment_route = segment_route.to_crs("EPSG:4326") # Project to latitude, longitude
    # Build the GPX of reference segment
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack()
    segment = gpxpy.gpx.GPXTrackSegment()
    gpx.tracks.append(track)
    track.segments.append(segment)
    for coordinate in segment_route.geometry.iloc[0].coords:
        segment.points.append(gpxpy.gpx.GPXTrackPoint(coordinate[1], coordinate[0]))
    # Existing matching and feature functions can read this just like an uploaded GPX
    name = "reference_segment_" + Path(gpx_name).name
    return gpx.to_xml().encode("utf-8"), name, segment_route


# Keep the same class colours on both maps
# Each class has a feature name, a label and a map colour
WORLDCOVER_DISPLAY = {
    10: ("tree_cover", "Tree cover", "#228b22"), 20: ("shrubland", "Shrubland", "#ffbb22"),
    30: ("grassland", "Grassland", "#70c76b"), 40: ("cropland", "Cropland", "#e6d54a"),
    50: ("built_up", "Built-up", "#e76f51"), 60: ("bare_sparse_vegetation", "Bare / sparse vegetation", "#c49a6c"),
    70: ("snow_ice", "Snow / ice", "#ffffff"), 80: ("permanent_water", "Permanent water", "#3182bd"),
    90: ("herbaceous_wetland", "Herbaceous wetland", "#009f75"), 95: ("mangroves", "Mangroves", "#00cf75"),
    100: ("moss_lichen", "Moss / lichen", "#b4d79e"),
}
# Match each dropdown choice to its raster file
DTM_DISPLAY = {
    "Elevation": ("dem_path", "gedtm30_alps.tif"),
    "Positive openness": ("positive_openness_path", "positive_openness_gedtm30_alps.tif"),
    "Negative openness": ("negative_openness_path", "negative_openness_gedtm30_alps.tif"),
    "Terrain roughness (SSDoN)": ("ssdon_path", "ssdon_gedtm30_alps.tif"),
}


@st.cache_data(max_entries=10)
def display_points(gpx_bytes):
    route = load_uploaded_route(gpx_bytes)
    points = resample_route(route, spacing_metres=30) # Enough detail without plotting every GPX point
    distance = np.arange(len(points), dtype=float) * 30 / 1000 # 0, 0.03, 0.06 km and so on
    # The last step may be shorter than 30 m, so use the real route length here
    distance[-1] = route.to_crs(route.estimate_utm_crs()).length.sum() / 1000
    return points, distance


@st.cache_data(max_entries=20) # More cache entries allowed because every combination of GPX and raster file needs its own cache entry
def display_raster_values(gpx_bytes, raster_path):
    points, distance = display_points(gpx_bytes)
    with rasterio.open(raster_path) as raster:
        values = get_finish_heights_from_dem(points, raster) # This also handles missing raster values
    return np.array(values) # Makes it easy to pick out valid values later


@st.cache_data(max_entries=10)
def display_worldcover(gpx_bytes, year, buffer_metres):
    # The existing function downloads missing tiles and calculates buffered area shares
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "display.gpx"
        path.write_bytes(gpx_bytes) # This file disappears when the temporary folder closes
        shares = extract_worldcover_features(path, buffer_metres=buffer_metres, worldcover_year=year)
    points, distance = display_points(gpx_bytes)
    classes = np.full(len(points), np.nan) # Start with missing values until a tile supplies a class
    for path in sorted(Path("../data/raw/worldcover").glob("ESA_WorldCover_10m_" + str(year) + "_*_Map.tif")):
        with rasterio.open(path) as raster:
            projected = points.to_crs(raster.crs) # Compare the route and tile in the same coordinates
            left, bottom, right, top = projected.total_bounds # Extract GPX bounding box
            if right < raster.bounds.left or left > raster.bounds.right or top < raster.bounds.bottom or bottom > raster.bounds.top:
                continue # Do not read tiles from another area
            # Despite its name, this helper can read land-cover codes too
            values = get_finish_heights_from_dem(points, raster)
            for index, value in enumerate(values):
                if value in WORLDCOVER_DISPLAY: # Ignore missing or unknown class codes
                    classes[index] = value
    return classes, shares # Point classes for the map, area shares for the table


def add_display_layer(traces, gpx_bytes, layer, dtm_name, column):
    try:
        points, distance = display_points(gpx_bytes)
        points = points.to_crs("EPSG:4326") # Latitude, longitude
        if layer == "WorldCover":
            options = FEATURE_OPTIONS["worldcover"]
            classes, shares = display_worldcover(gpx_bytes, options["worldcover_year"], options["buffer_metres"])
            rows = [] # Collect the table rows while adding the map colours
            for code, (name, label, colour) in WORLDCOVER_DISPLAY.items():
                selected = points[classes == code] # One colour for all points of this class
                if not selected.empty: # No need to add a legend entry for an absent class
                    traces.append(go.Scattermap(lon=selected.geometry.x, lat=selected.geometry.y,
                                                mode="markers", name=label, marker={"color": colour, "size": 6}))
                if shares.get(name, 0) > 0: # Leave zero-share classes out of the table
                    rows.append({"WorldCover class": label, "Share (%)": shares[name] * 100}) # Turn fractions into percentages
            if not np.isfinite(classes).all():
                column.warning("WorldCover coverage is incomplete; uncoloured sections have no class data.")
            return rows

        # DTM layer
        option, filename = DTM_DISPLAY[dtm_name] # Pick the raster for the selected DTM view
        path = FEATURE_OPTIONS["dtm"].get(option, Path("../data/raw/gedtm30") / filename)
        values = display_raster_values(gpx_bytes, path)
        valid = np.isfinite(values) # Only colour points where we actually have data
        if not valid.any(): # No usable values at all
            raise ValueError("No valid DTM values along this route.")
        traces.append(go.Scattermap(lon=points.geometry.x[valid], lat=points.geometry.y[valid], mode="markers",
                                    name=dtm_name, marker={"size": 6, "color": values[valid], "colorscale": "Viridis",
                                    "showscale": True, "colorbar": {"title": dtm_name}}))
        if not valid.all(): # Some points are missing, but the rest can still be shown
            column.warning("DTM coverage is incomplete; missing values are not coloured.")
    except Exception as error:
        column.warning("Could not display this layer: " + str(error)) # Keep the plain route visible
    return []


def render_layer_map(traces, route, route_number):
    bounds = route.to_crs("EPSG:4326").total_bounds
    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 0.001) # Use the larger side to choose the zoom
    figure = go.Figure(traces) # Put the route, colours and markers onto one map
    # Keep the satellite background, but leave more room for the profile below
    figure.update_layout(height=520, margin={"l": 5, "r": 5, "t": 5, "b": 5},
                         legend={"orientation": "h", "y": 1.02, "yanchor": "bottom"},
                         map={"style": "white-bg", "zoom": max(3, min(16, 8 - np.log2(span))),
                              "center": {"lon": (bounds[0] + bounds[2]) / 2, "lat": (bounds[1] + bounds[3]) / 2},
                              "layers": [{"below": "traces", "sourcetype": "raster", "source": [
                                  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"]}]})
    # Separate keys let both route maps appear together
    st.plotly_chart(figure, width="stretch", key="route_map_" + str(route_number), config={"displaylogo": False})


def show_elevation_profile(gpx_bytes, route_number):
    try:
        points, distance = display_points(gpx_bytes)
        path = FEATURE_OPTIONS["dtm"].get("dem_path", Path("../data/raw/gedtm30/gedtm30_alps.tif"))
        heights = display_raster_values(gpx_bytes, path)
        if not np.isfinite(heights).any():
            raise ValueError("No valid elevation data along this route.")
        profile = pd.DataFrame({"Distance (km)": distance, "Elevation (m)": heights}) # Pair each distance with its height
        st.line_chart(profile, x="Distance (km)", y="Elevation (m)", height=260) # Use Streamlit's simple chart
        if not np.isfinite(heights).all():
            st.caption("Gaps in the profile indicate missing elevation data.")  # Showing elevation profile also if elevation data is missing for some GPX points
    except Exception as error:
        st.warning("Could not display the elevation profile: " + str(error))


def show_route(route_source, column, route_number, generated_route=None):
    """Load and display one route with its optional OSM graph and matching."""
    # Keep the existing labels, which Streamlit also uses to identify widgets. Configuring labels according to route_number makes the rest of the code below more concise.
    labels = {
        "gpx": "Upload GPX file", "graphml": "Upload GraphML file",
        "race": "Race", "tag": "OSM tag", "values": "OSM tag value",
        "gpx_message": "Loaded GPX: ", "graphml_message": "Loaded GraphML: ",
    }
    if route_number == 2:
        labels = {
            "gpx": "Upload Route 2 GPX file", "graphml": "Upload Route 2 GraphML file",
            "race": "Race 2", "tag": "Route 2 OSM tag", "values": "Route 2 OSM tag value",
            "gpx_message": "Route 2 GPX: ", "graphml_message": "Route 2 GraphML: ",
        }
    gpx_bytes = None
    graphml_bytes = None
    gpx_name = ""
    graphml_name = ""

    try:
        # Show generated routes in the same way as uploaded routes.
        if route_source == "Generate route":
            if generated_route is None:
                return None
            gpx_bytes = generated_route["gpx_bytes"]
            graphml_bytes = generated_route["graphml_bytes"]
            gpx_name = generated_route["gpx_name"]
            graphml_name = "Generated walking network"
        elif route_source == "Upload files":
            uploaded_gpx = st.sidebar.file_uploader(labels["gpx"], type=["gpx"], key="gpx_" + str(route_number)) # Show Streamlit file upload element if "Upload files" is selected
            uploaded_graphml = st.sidebar.file_uploader(labels["graphml"], type=["graphml"], key="graphml_" + str(route_number))
            if uploaded_gpx is not None:
                gpx_bytes = uploaded_gpx.getvalue() # Read the uploaded GPX file contents as bytes to enable later processing in load_uploaded_file()
                gpx_name = uploaded_gpx.name
            if uploaded_graphml is not None:
                graphml_bytes = uploaded_graphml.getvalue()
                graphml_name = uploaded_graphml.name
        else:
            # Use the existing race folders in the project if user chooses this option
            races_folder = Path("data/races")
            if not races_folder.exists():
                races_folder = Path(__file__).resolve().parents[1] / "data" / "races"  # If data/races does not exist relative to the working directory, search the folder relative to this Streamlit file
            gpx_files = sorted(races_folder.glob("*/*.gpx"))
            if not gpx_files:
                st.info("No GPX files found in data/races. Start the app from the project folder.")
            else:
                selected_gpx = st.sidebar.selectbox(labels["race"], gpx_files, key="race_" + str(route_number)) # Alphabetically sorted dropdown of stored races
                gpx_bytes = selected_gpx.read_bytes()
                gpx_name = selected_gpx.name
                graphml_path = selected_gpx.with_name(selected_gpx.stem + "_retain_all.graphml") # Stored GraphML files all have _retain_all extension to make download parameters clear
                if graphml_path.exists():
                    graphml_bytes = graphml_path.read_bytes()
                    graphml_name = graphml_path.name
                else:
                    st.info("No GraphML file found for this race. Showing the GPX route only.")

        if gpx_bytes is None:
            st.info("Select or upload a GPX file to display a route.")
        else: # Starts once a GPX is available
            route_gdf = load_uploaded_route(gpx_bytes)
            full_reference = None
            if route_number == 1 and st.sidebar.checkbox("Use reference segment", value=False): # User wants to use segment only
                full_reference = route_gdf # Keep the whole route in the background of the map
                selected = select_reference_segment(route_gdf, gpx_bytes, gpx_name)
                if selected is None:
                    return None
                gpx_bytes, gpx_name, route_gdf = selected # All following steps now use the segment as GPX. Tuple unpacking
            st.success(labels["gpx_message"] + gpx_name) # Display success banner in UI
            traces: list[go.Scattermap] = []
            if full_reference is not None:
                longitudes, latitudes = [], []
                for coordinate in full_reference.geometry.iloc[0].coords:
                    longitudes.append(coordinate[0])
                    latitudes.append(coordinate[1])
                traces.append(go.Scattermap(lon=longitudes, lat=latitudes, mode="lines",
                                            line={"color": "grey", "width": 2}, name="Full reference route"))
            add_gpx(traces, route_gdf)
            layer_rows = []
            selected_tag = "None"
            matched_edges = None
            if full_reference is not None:
                traces[-1].line.color = "purple" # Make the selected reference segment easy to spot
                traces[-1].name = "Reference segment"
            if display_layer != "OpenStreetMap":
                layer_rows = add_display_layer(traces, gpx_bytes, display_layer, dtm_name, column)
            if generated_route is None:
                ends = list(route_gdf.geometry.iloc[0].coords)
                traces.append(go.Scattermap(lon=[ends[0][0], ends[-1][0]], lat=[ends[0][1], ends[-1][1]],
                                            mode="markers", marker={"size": 10, "color": ["blue", "green"]}, name="Start / finish"))

            if graphml_bytes is not None and display_layer == "OpenStreetMap": # Match only for the OSM layer
                osm_graph = load_uploaded_graph(graphml_bytes)
                st.success(labels["graphml_message"] + graphml_name)
                selected_tag = st.sidebar.selectbox(labels["tag"], ["None"] + OSM_TAGS, key="tag_" + str(route_number))
                selected_values = []

                if selected_tag != "None":
                    osm_edges = ox.graph_to_gdfs(osm_graph, nodes=False, edges=True) # Convert OSM graph to GeoDataFrame fur further processing
                    if selected_tag not in osm_edges.columns:
                        st.sidebar.info("OpenStreetMap graph has no values for the selected tag.")
                        selected_tag = "None"
                    else:
                        # Convert values to strings, remove duplicates and sort.
                        tag_values = osm_edges[selected_tag].dropna().astype(str).unique()
                        tag_values = sorted(tag_values)

                        if selected_tag == "sac_scale":
                            selected_values = st.sidebar.multiselect(
                                labels["values"],
                                tag_values,
                                format_func=format_sac_scale, # Apply special formatting to SAC scale to account for ordinal characteristics of the feature
                                key="values_" + str(route_number), # Make selection specific to reference route
                            )
                        else:
                            selected_values = st.sidebar.multiselect(
                                labels["values"],
                                tag_values,
                                key="values_" + str(route_number), # Make selection specific to generated route
                            )

                if show_network: # The full network can hide the coloured route
                    add_graph(traces, osm_graph)
                with st.spinner("Matching GPX points to OSM edges..."): # Show in UI that matching is ongoing. Spinner stops when code execution in with-block is completed
                    matched_edges = get_matched_edges(gpx_bytes, graphml_bytes, gpx_name)
                if selected_tag != "None" and not selected_values and selected_tag in matched_edges.columns:
                    selected_values = sorted(matched_edges[selected_tag].dropna().astype(str).unique()) # Colour all values by default
                add_matched_edges(traces, matched_edges, selected_tag, selected_values)

            if generated_route is not None and st.sidebar.checkbox("Show start, finish and waypoints", value=True):
                markers = generated_route["markers"]
                for index, point in enumerate(markers): # Extract Streamlit markers for start and finish
                    label = "Waypoint " + str(index)
                    colour = "purple"
                    if index == 0:
                        label, colour = "Start", "blue"
                    elif index == len(markers) - 1:
                        label, colour = "Finish", "green"
                    # A round trip only needs one marker for start and finish
                    if index == 0 and markers[0] == markers[-1]:
                        label = "Start / finish"
                    if index == len(markers) - 1 and markers[0] == markers[-1]:
                        continue
                    traces.append(go.Scattermap(lon=[point[0]], lat=[point[1]], mode="markers",
                                                marker={"size": 11, "color": colour}, name=label))

            if traces:
                if route_number == 2:
                    # Different trace names prevent duplicate charts for identical routes.
                    for trace in traces:
                        trace.name = "Route 2: " + trace.name
                with column: # Indented code is only executed for the respective Streamlit container
                    # Calculate the GPX length in metres before converting to kilometres.
                    metric_crs = route_gdf.estimate_utm_crs()
                    metric_route = route_gdf.to_crs(metric_crs)
                    distance_km = metric_route.length.sum() / 1000
                    st.metric("Distance", str(round(distance_km, 2)) + " km")
                    st.write(display_layer)
                    render_layer_map(traces, route_gdf, route_number)
                    if show_profile:
                        show_elevation_profile(gpx_bytes, route_number)
                    if display_layer == "WorldCover" and layer_rows: # ESA WorldCover
                        st.write("WorldCover")
                        st.dataframe(pd.DataFrame(layer_rows), hide_index=True, width="stretch") # Show table with WorldCover class shares
                        st.caption("Map: classes sampled every 30 m. Table: area shares in the route buffer.")
                    if matched_edges is not None and selected_tag in matched_edges.columns: # OSM
                        table = matched_edges.copy()
                        table["Tag value"] = table[selected_tag].fillna("Missing").astype(str)
                        lengths = table.to_crs(table.estimate_utm_crs()).length
                        if lengths.sum() > 0: # Show table with OSM tag value shares
                            table["Share (%)"] = lengths / lengths.sum() * 100
                            table = table.groupby("Tag value", as_index=False)["Share (%)"].sum()
                            if selected_tag == "sac_scale":
                                table["Tag value"] = table["Tag value"].apply(format_sac_scale)
                            st.write("OpenStreetMap")
                            st.dataframe(table, hide_index=True, width="stretch")
                            st.caption("Shares of matched edge length, including missing tags.")
            else:
                st.warning("No plottable data found.") # Only display warning that there is no data for plotting. User is able to upload a different GPX file / OSM file.

            return {
                "gpx_bytes": gpx_bytes,
                "graphml_bytes": graphml_bytes,
                "gpx_name": gpx_name,
                "features": generated_route["features"] if generated_route is not None else None, # Uploads are measured later
            }
    except Exception as e:
        st.error("Could not read the route data: " + str(e)) # Ensures that error message appears in the app in case of a problem

    return None


st.set_page_config(layout="wide") # Use entire screen width (relevant for wider screen such as my 21:9 monitor for video presentation)
st.title("Trail route comparison")
st.write("Select a saved race or upload a GPX route and an optional GraphML file.")
# One layer choice applies to both routes, making comparison easier
display_layer = st.sidebar.radio("Layer", ["OpenStreetMap", "WorldCover", "Digital Terrain Model"])
dtm_name = "Elevation"
if display_layer == "Digital Terrain Model":
    dtm_name = st.sidebar.selectbox("DTM values", list(DTM_DISPLAY))
show_profile = st.sidebar.checkbox("Show elevation profile", value=True)
show_network = st.sidebar.checkbox("Show full OSM network", value=False)
st.sidebar.header("Route")
route_source = st.sidebar.radio("Route source", ["Upload files", "Saved race"], key="route_1_source") # Single option selection with "radio" buttons

compare_routes = st.sidebar.checkbox("Compare a second route", key="compare_routes")
if compare_routes:
    left, right = st.columns(2) # Switch to Streamlit colunm layout if route comparison is selected
else:
    left = st.container()

route_1 = show_route(route_source, left, 1) # Displaying reference route in the left app column

if compare_routes:
    st.sidebar.header("Route 2")
    route_source = st.sidebar.radio("Route 2 source", ["Upload files", "Saved race", "Generate route"], key="route_2_source") # Single option selection with "radio" buttons
    generated_route = None
    if route_source == "Generate route":
        with right:
            generated_route = select_generated_route(route_1)
    route_2 = show_route(route_source, right, 2, generated_route) # Displaying generated route in the right app column

    if route_1 is not None and route_2 is not None:
        try:
            show_feature_comparison(route_1, route_2)
        except Exception as e:
            st.warning("Could not compare route features: " + str(e))
