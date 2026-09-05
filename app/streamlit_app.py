import streamlit as st
from trail_route_recommendation.data.gpx import load_gpx
#from trail_route_recommendation.matching.osm import (
#    match_gpx_to_edges,
#    get_matched_edges,
#)
import plotly.graph_objects as go
import os
import tempfile
import osmnx as ox

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
                go.Scatter(
                    x=lons,
                    y=lats,
                    mode="lines",
                    name="GPX trajectory",
                    line=dict(width=4),
                    hoverinfo="skip",
                )
            )

            st.subheader("GPX")
            st.plotly_chart(
                fig,
                width="stretch",
                config={"displaylogo": False},
            )
        else:
            st.warning("No plottable data found.")


        # Plot GraphML edges using node coordinates
        if uploaded_graphml:
            fd, graph_path = tempfile.mkstemp(suffix=".graphml")

            try:
                with os.fdopen(fd, "wb") as tmp:
                    tmp.write(uploaded_graphml.getvalue())

                graph = ox.load_graphml(graph_path)

            finally:
                os.remove(graph_path)

            edge_lons = []
            edge_lats = []

            for u, v in graph.edges():
                node_u = graph.nodes[u]
                node_v = graph.nodes[v]

                x1 = node_u.get("x")
                y1 = node_u.get("y")
                x2 = node_v.get("x")
                y2 = node_v.get("y")

                if None not in (x1, y1, x2, y2):
                    edge_lons.extend([float(x1), float(x2), None])
                    edge_lats.extend([float(y1), float(y2), None])

            if edge_lons:
                traces.append(
                    go.Scatter(
                        x=edge_lons,
                        y=edge_lats,
                        mode="lines",
                        name="GraphML",
                        line=dict(width=1),
                        hoverinfo="skip",
                    )
                )

        if traces:
            fig = go.Figure(traces)

            fig.update_layout(
                height=650,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                showlegend=True,
                xaxis=dict(
                    title="Longitude",
                    showgrid=False,
                    zeroline=False,
                ),
                yaxis=dict(
                    title="Latitude",
                    showgrid=False,
                    zeroline=False,
                    scaleanchor="x",
                    scaleratio=1,
                ),
            )

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