import streamlit as st
import plotly.graph_objects as go
import osmnx as ox
from trail_route_recommendation.data.osm_pruning_debugging import format_sac_scale
from ui.settings import OSM_TAGS
from ui.data_loading import load_uploaded_route, load_uploaded_graph, get_matched_edges
from ui.route_display import add_gpx, add_graph, add_matched_edges, render_map


st.title("GPX route viewer")
st.write("Upload a GPX route and optionally a GraphML file for OSM edge matching.")
st.sidebar.header("Route")
uploaded_gpx = st.sidebar.file_uploader("Upload GPX file", type=["gpx"])
uploaded_graphml = st.sidebar.file_uploader("Upload GraphML file", type=["graphml"])

if uploaded_gpx is None:
    st.info("Upload a GPX file to display a route.")
else: # Starts once user uploaded a GPX
    try:
        gpx_bytes = uploaded_gpx.getvalue()
        route_gdf = load_uploaded_route(gpx_bytes)
        st.success("Loaded GPX: " + uploaded_gpx.name) # Display success banner in UI
        traces: list[go.Scattermap] = []
        add_gpx(traces, route_gdf)

        if uploaded_graphml is not None: # Starts once user uploaded a GraphML file
            graphml_bytes = uploaded_graphml.getvalue()
            osm_graph = load_uploaded_graph(graphml_bytes)
            st.success("Loaded GraphML: " + uploaded_graphml.name)
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
                matched_edges = get_matched_edges(gpx_bytes, graphml_bytes, uploaded_gpx.name)
            add_matched_edges(traces, matched_edges, selected_tag, selected_values)

        if traces:
            render_map(traces, route_gdf)
        else:
            st.warning("No plottable data found.") # Only display warning that there is no data for plotting. User is able to upload a different GPX file / OSM file.
    except Exception as error:
        st.error("Could not read the uploaded data: " + str(error))
