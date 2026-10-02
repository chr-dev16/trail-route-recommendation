import streamlit as st
from pathlib import Path
import plotly.graph_objects as go
import osmnx as ox
from trail_route_recommendation.data.osm_pruning_debugging import format_sac_scale
from ui.settings import OSM_TAGS
from ui.data_loading import load_uploaded_route, load_uploaded_graph, get_matched_edges
from ui.route_display import add_gpx, add_graph, add_matched_edges, render_map


st.title("GPX route viewer")
st.write("Select a saved race or upload a GPX route and an optional GraphML file.")
st.sidebar.header("Route")
route_source = st.sidebar.radio("Route source", ["Upload files", "Saved race"]) # Single option selection with "radio" buttons
gpx_bytes = None
graphml_bytes = None
gpx_name = ""
graphml_name = ""

try:
    if route_source == "Upload files": # 
        uploaded_gpx = st.sidebar.file_uploader("Upload GPX file", type=["gpx"]) # Show Streamlit file upload element if "Upload files" is selected
        uploaded_graphml = st.sidebar.file_uploader("Upload GraphML file", type=["graphml"])
        if uploaded_gpx is not None:
            gpx_bytes = uploaded_gpx.getvalue() # Read the uploaded GPX file contents as bytes to enable later processing in load_uploaded_file()
            gpx_name = uploaded_gpx.name
        if uploaded_graphml is not None:
            graphml_bytes = uploaded_graphml.getvalue()
            graphml_name = uploaded_graphml.name
    else:
        # Use the existing race folders in the project
        races_folder = Path("data/races")
        gpx_files = sorted(races_folder.glob("*/*.gpx"))
        if not gpx_files:
            st.info("No GPX files found in data/races. Start the app from the project folder.")
        else:
            selected_gpx = st.sidebar.selectbox("Race", gpx_files) # Alphabetically sorted dropdown of stored races
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
        st.success("Loaded GPX: " + gpx_name) # Display success banner in UI
        traces: list[go.Scattermap] = []
        add_gpx(traces, route_gdf)

        if graphml_bytes is not None: # Starts once a GraphML file is available
            osm_graph = load_uploaded_graph(graphml_bytes)
            st.success("Loaded GraphML: " + graphml_name)
            selected_tag = st.sidebar.selectbox("OSM tag", ["None"] + OSM_TAGS)
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
                            "OSM tag value",
                            tag_values,
                            format_func=format_sac_scale, # Apply special formatting to SAC scale to account for ordinal characteristics of the feature
                        )
                    else:
                        selected_values = st.sidebar.multiselect(
                            "OSM tag value",
                            tag_values,
                        )

            add_graph(traces, osm_graph)
            with st.spinner("Matching GPX points to OSM edges..."): # Show in UI that matching is ongoing
                matched_edges = get_matched_edges(gpx_bytes, graphml_bytes, gpx_name)
            add_matched_edges(traces, matched_edges, selected_tag, selected_values)

        if traces:
            render_map(traces, route_gdf)
        else:
            st.warning("No plottable data found.") # Only display warning that there is no data for plotting. User is able to upload a different GPX file / OSM file.
except Exception as error:
    st.error("Could not read the route data: " + str(error))
