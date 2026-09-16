import io
import os
import tempfile
import streamlit as st
import osmnx as ox
import geopandas as gpd
import networkx
from trail_route_recommendation.data.gpx import load_gpx, resample_route
from trail_route_recommendation.data.osm_pruning_debugging import match_gpx_to_edges, get_protected_nodes, remove_short_deadends


@st.cache_data(max_entries=10)
def load_uploaded_route(gpx_bytes: bytes) -> gpd.GeoDataFrame:
    ''' Cache by file contents, so changing a display option does not reload the GPX. (Since Streamlit executes app script each time when user input is changed.) '''
    file = io.StringIO(gpx_bytes.decode("utf-8-sig"))
    return load_gpx(file)


@st.cache_data(max_entries=10)
def load_uploaded_graph(graphml_bytes: bytes) -> networkx.MultiDiGraph:
    ''' Cache by file contents, so changing a display option does not reload the GraphML file. (Since Streamlit executes app script each time when user input is changed.) '''

    # Create a temporary file and get its file descriptor and path.
    fd_temp, graph_path_temp = tempfile.mkstemp(suffix=".graphml")
    try:
        # Write the uploaded bytes to the temporary file and close it.
        with os.fdopen(fd_temp, "wb") as file:
            file.write(graphml_bytes)

        # Load the graph from the file because OSMnx expects a file path.
        return ox.load_graphml(graph_path_temp)
    
    finally:
        os.remove(graph_path_temp) # Delete the temporary file, even if loading the graph fails.


@st.cache_data(max_entries=10)
def get_matched_edges(gpx_bytes: bytes, graphml_bytes: bytes, gpx_name: str) -> gpd.GeoDataFrame:
    ''' Cache by GPX/OSM combination, so changing a display option does not re-trigger the GPX-OSM matching. (Since Streamlit executes app script each time when user input is changed.) '''

    route_gdf = load_uploaded_route(gpx_bytes)
    osm_graph = load_uploaded_graph(graphml_bytes)

    # Resample equi-spaced points on GPX trajectory
    route_points = resample_route(
        route_gdf,
        spacing_metres=30,
    )

    # Perform actual matching
    matched_edges = match_gpx_to_edges(
        route_points,
        osm_graph,
    )

    print("\n" + "=" * 60)
    print(f"MATCHING BEFORE DEADEND PRUNING: {gpx_name}")
    print(f"Matched edges: {len(matched_edges):,}")
    print(f"Matched edge length: {matched_edges['length'].sum() / 1000:.2f} km")

    if "surface" in matched_edges.columns:
        surface_edges = matched_edges[matched_edges["surface"].notna()]
        print(f"Surface edges: {len(surface_edges):,}")
        print(f"Surface edge length: {surface_edges['length'].sum() / 1000:.2f} km")

    # Ensure that edges adjacent to protected nodes (start and end of race) are not removed from graph
    protected_nodes = get_protected_nodes(
        route_points,
        osm_graph,
    )

    # Pruning of (genuine) deadends from OSM GeoDataFrame
    matched_edges = remove_short_deadends(
        matched_edges,
        osm_graph,
        protected_nodes,
    )

    print(f"MATCHING AFTER DEADEND PRUNING: {gpx_name}")
    print(f"Matched edges: {len(matched_edges):,}")
    print(f"Matched edge length: {matched_edges['length'].sum() / 1000:.2f} km")

    if "surface" in matched_edges.columns:
        surface_edges = matched_edges[matched_edges["surface"].notna()]
        print(f"Surface edges: {len(surface_edges):,}")
        print(f"Surface edge length: {surface_edges['length'].sum() / 1000:.2f} km")

    print("=" * 60)

    return matched_edges


