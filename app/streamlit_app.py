import streamlit as st
from trail_route_recommendation.data.gpx import load_gpx
import plotly.graph_objects as go

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