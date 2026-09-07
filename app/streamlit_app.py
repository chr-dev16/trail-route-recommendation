import streamlit as st
from trail_route_recommendation.data.gpx import load_gpx, resample_route
from trail_route_recommendation.data.osm import match_gpx_to_edges, format_sac_scale
import plotly.graph_objects as go
import os
import tempfile
import osmnx as ox
import numpy as np

OSM_TAG_COLORS = [
    "#FF6B6B",
    "#4D96FF",
    "#6BCB77",
    "#FFD93D",
    "#B983FF",
    "#FF922B",
    "#20C997",
    "#F06595",
]

SAC_SCALE_COLORS = {
    "hiking": "#2ECC71",                        # T1
    "mountain_hiking": "#A8D03B",               # T2
    "demanding_mountain_hiking": "#F1C40F",     # T3
    "alpine_hiking": "#F39C12",                 # T4
    "demanding_alpine_hiking": "#E67E22",       # T5
    "difficult_alpine_hiking": "#E74C3C",       # T6
}

st.title("Trail Route Generation (dev)")

st.write("Development/debugging interface for trail route generation.")

st.sidebar.header("Route")

uploaded_gpx = st.sidebar.file_uploader(
    "Upload GPX file",
    type=["gpx"]
)

uploaded_graphml = st.sidebar.file_uploader(
    "Upload GraphML file",
    type=["graphml"]
)

osm_tags = [
    "highway",
    "surface",
    "tracktype",
    "sac_scale",
    "smoothness",
    "trail_visibility",
    "mtb:scale",
    "trailblazed",
    "incline",
    "step_count",
    "width",
    "informal",
    "hazard",
    "foot",
    "access",
    "barrier",
    "bridge",
    "tunnel",
    "ford",
]

selected_tag = st.sidebar.selectbox(
    "OSM tag",
    ["None"] + osm_tags,
)

selected_values = []

if uploaded_gpx or uploaded_graphml:
    if uploaded_gpx:
        st.success(f"Loaded GPX: {uploaded_gpx.name}")

    if uploaded_graphml:
        st.success(f"Loaded GraphML: {uploaded_graphml.name}")

    try:
        traces = []

        # Plot GPX trajectory
        if uploaded_gpx:
            route_gdf = load_gpx(uploaded_gpx)

            for geometry in route_gdf.geometry:
                lons, lats = geometry.xy

                traces.append(
                    go.Scattermap(
                        lon=np.array(lons),
                        lat=np.array(lats),
                        mode="lines",
                        name="GPX trajectory",
                        line=dict(width=4),
                        hoverinfo="skip",
                    )
                )

        # Plot GraphML edges using node coordinates
        if uploaded_graphml:
            fd, graph_path = tempfile.mkstemp(suffix=".graphml")

            try:
                with os.fdopen(fd, "wb") as tmp:
                    tmp.write(uploaded_graphml.getvalue())

                osm_graph = ox.load_graphml(graph_path)

            finally:
                os.remove(graph_path)

            if selected_tag != "None":
                osm_edges = ox.graph_to_gdfs(
                    osm_graph,
                    nodes=False,
                    edges=True,
                )

                tag_values = sorted(
                    {
                        str(value)
                        for value in osm_edges[selected_tag].dropna()
                    }
                )

                tag_value_options = {}

                if selected_tag == "sac_scale":
                    for value in tag_values:
                        displayed_value = format_sac_scale(value)

                        tag_value_options[displayed_value] = value
                else:
                    for value in tag_values:
                        tag_value_options[value] = value

                selected_labels = st.sidebar.multiselect(
                    "OSM tag value",
                    tag_value_options.keys(),
                )

                selected_values = [
                    tag_value_options[label]
                    for label in selected_labels
                ]

            edge_lons = []
            edge_lats = []

            for u, v in osm_graph.edges():
                node_u = osm_graph.nodes[u]
                node_v = osm_graph.nodes[v]

                x1 = node_u.get("x")
                y1 = node_u.get("y")
                x2 = node_v.get("x")
                y2 = node_v.get("y")

                if None not in (x1, y1, x2, y2):
                    edge_lons.extend([float(x1), float(x2), None])
                    edge_lats.extend([float(y1), float(y2), None])

            if edge_lons:
                traces.append(
                    go.Scattermap(
                        lon=edge_lons,
                        lat=edge_lats,
                        mode="lines",
                        name="GraphML",
                        line=dict(width=1),
                        hoverinfo="skip",
                    )
                )

        # Match resampled GPX points to osm_graph edges
        if uploaded_gpx and uploaded_graphml:
            route_points = resample_route(
                route_gdf,
                spacing_metres=30,
            )

            matched_edges = match_gpx_to_edges(
                route_points,
                osm_graph,
            )

            # Show only matched edges with the selected OSM tag/value
            if selected_tag != "None" and selected_tag in matched_edges.columns:
                matched_edges = matched_edges[
                    matched_edges[selected_tag].notna()
                ]

                if selected_values:
                    matched_edges = matched_edges[
                        matched_edges[selected_tag].astype(str).isin(
                            selected_values
                        )
                    ]

            # Convert matched edges back to longitude/latitude for plotting
            matched_edges = matched_edges.to_crs("EPSG:4326")

            matched_lons = []
            matched_lats = []

            for geometry in matched_edges.geometry:
                if geometry is not None:
                    if geometry.geom_type == "LineString":
                        lons, lats = geometry.xy
                        matched_lons.extend([*lons, None])
                        matched_lats.extend([*lats, None])

            if selected_values and selected_tag != "None":
                for i, value in enumerate(selected_values):
                    value_edges = matched_edges[
                        matched_edges[selected_tag].astype(str) == value
                    ]

                    value_lons = []
                    value_lats = []

                    for geometry in value_edges.geometry:
                        if geometry is not None and geometry.geom_type == "LineString":
                            lons, lats = geometry.xy
                            value_lons.extend([*lons, None])
                            value_lats.extend([*lats, None])

                    if value_lons:
                        if selected_tag == "sac_scale":
                            display_value = format_sac_scale(value)
                        else:
                            display_value = value

                        traces.append(
                            go.Scattermap(
                                lon=value_lons,
                                lat=value_lats,
                                mode="lines",
                                name=f"{selected_tag} = {display_value}",
                                line=dict(
                                    width=3,
                                    color=(
                                        SAC_SCALE_COLORS.get(value)
                                        if selected_tag == "sac_scale"
                                        else OSM_TAG_COLORS[i % len(OSM_TAG_COLORS)]
                                    ),
                                ),
                                hoverinfo="skip",
                            )
                        )

            elif matched_lons:
                traces.append(
                    go.Scattermap(
                        lon=matched_lons,
                        lat=matched_lats,
                        mode="lines",
                        name=f"Matched edges: {selected_tag}",
                        line=dict(width=3),
                        hoverinfo="skip",
                    )
                )

        if traces:
            fig = go.Figure(traces)

            # Centre the map on the uploaded GPX route
            bounds = route_gdf.total_bounds

            center_lon = (bounds[0] + bounds[2]) / 2
            center_lat = (bounds[1] + bounds[3]) / 2

            # Determine max. dimension of the route's bounding box
            max_range = max(
                bounds[2] - bounds[0],
                bounds[3] - bounds[1],
            )

            # Adaptive zoom
            if max_range < 0.01:
                zoom = 16
            elif max_range < 0.03:
                zoom = 14
            elif max_range < 0.1:
                zoom = 12
            else:
                zoom = 10

            # Apply centering and add satellite raster image underneath the GPX, OSM, ... layers
            fig.update_layout(
                height=650,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="left",
                    x=0,
                ),
                map=dict(
                    style="white-bg",
                    center=dict(
                        lon=center_lon,
                        lat=center_lat,
                    ),
                    zoom=zoom,
                    layers=[
                        dict(
                            below="traces",
                            sourcetype="raster",
                            source=[
                                "https://server.arcgisonline.com/ArcGIS/rest/services/"
                                "World_Imagery/MapServer/tile/{z}/{y}/{x}"
                            ],
                        )
                    ],
                ),
            )

            st.subheader("GPX and OSM graph")
            st.plotly_chart(
                fig,
                width="stretch",
                config={"displaylogo": False},
            )
        else:
            st.warning("No plottable data found.")


    except Exception as e:
        st.error(f"Could not read the uploaded data: {e}")

races = [
    "Select a race...",
    "Istria 168K",
    "Istria 110K",
    "Istria 69K",
    "JAT 120K",
    "JAT 80K",
    "JAT 55K",
    "Hochkönig HK70",
    "IATF K25",
]

selected_race = st.sidebar.selectbox(
    "Race",
    races,
    index=0,
    help="Select a race to inspect or use as the basis for route generation.",
)

if selected_race != "Select a race...":
    st.success(f"Selected race: **{selected_race}**")