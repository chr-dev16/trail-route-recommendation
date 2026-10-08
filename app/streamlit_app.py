import streamlit as st
from pathlib import Path
import json
import tempfile
import pandas as pd
import plotly.graph_objects as go
import osmnx as ox
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
    st.caption("Complete routes; DTM, WorldCover and OSM weighted equally. Distance is shown but not scored.")

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
    mode = st.sidebar.selectbox("Finish", ["Round trip", "Choose coordinates"])
    end_latitude, end_longitude = None, None # A round trip ends at the start
    if mode == "Choose coordinates":
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
    st.sidebar.caption(mode + ": " + str(minimum) + "-" + str(maximum)
                       + " km. Search uses distance and a penalty for repetition above 10%.")
    if minimum > maximum: # Stop here if the range does not make sense
        st.warning("Minimum distance must not exceed maximum distance.")
        return None
    # Saving the settings which were used for these routes
    settings = (reference["gpx_bytes"], latitude, longitude, half_width, attempts, seed, minimum, maximum,
                mode, end_latitude, end_longitude)
    if st.sidebar.button("Generate routes"): # Below runs once user starts route generation
        st.session_state.pop("generated_result", None)  # Old results are cleared before re-start
        try:
            with st.spinner("Loading the OSM network and searching for routes..."):
                graph, graph_bytes = load_generation_graph(latitude, longitude, half_width,
                                                            end_latitude, end_longitude)
                # Find the nearest point in the OSM network.
                start_node = ox.distance.nearest_nodes(graph, X=longitude, Y=latitude)
                end_node = start_node
                if mode == "Choose coordinates":
                    end_node = ox.distance.nearest_nodes(graph, X=end_longitude, Y=end_latitude)
                    if end_node == start_node: # Both coordinates ended up at the same OSM node
                        raise ValueError("Start and finish are too close. Choose another finish or use Round trip.")
                # Generate routes by distance and repeated path share
                candidates = generate_routes(graph, start_node, {"distance_m": reference_distance},
                                             attempts=attempts, seed=seed, end=end_node,
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
    st.caption("Distance error: " + str(round(candidate["error"], 4))
                + " | Repeated distance: " + str(round(candidate["repeat_fraction"] * 100, 1)) + "%"
                + " | Penalty: " + str(round(candidate["repeat_penalty"], 4))
                + " | Search score: " + str(round(candidate["total_error"], 4)))
    name = "generated_route_" + str(index + 1) + ".gpx"
    gpx_bytes = result["gpx_files"][index]
    st.download_button("Download generated GPX", gpx_bytes, file_name=name,
                       mime="application/gpx+xml")
    return {"gpx_bytes": gpx_bytes, "graphml_bytes": result["graph_bytes"], "gpx_name": name,
            "markers": candidate["markers"]} # Pass the points to the map below


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
            st.success(labels["gpx_message"] + gpx_name) # Display success banner in UI
            traces: list[go.Scattermap] = []
            add_gpx(traces, route_gdf)

            if graphml_bytes is not None: # Starts once a GraphML file is available
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

                add_graph(traces, osm_graph)
                with st.spinner("Matching GPX points to OSM edges..."): # Show in UI that matching is ongoing. Spinner stops when code execution in with-block is completed
                    matched_edges = get_matched_edges(gpx_bytes, graphml_bytes, gpx_name)
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
                    render_map(traces, route_gdf)
            else:
                st.warning("No plottable data found.") # Only display warning that there is no data for plotting. User is able to upload a different GPX file / OSM file.

            return {
                "gpx_bytes": gpx_bytes,
                "graphml_bytes": graphml_bytes,
                "gpx_name": gpx_name,
            }
    except Exception as e:
        st.error("Could not read the route data: " + str(e)) # Ensures that error message appears in the app in case of a problem

    return None


st.set_page_config(layout="wide") # Use entire screen width (relevant for wider screen such as my 21:9 monitor for video presentation)
st.title("GPX route viewer")
st.write("Select a saved race or upload a GPX route and an optional GraphML file.")
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
