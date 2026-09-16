import streamlit as st
import plotly.graph_objects as go
import geopandas as gpd
import numpy as np
import networkx
from trail_route_recommendation.data.osm_pruning_debugging import format_sac_scale
from ui.settings import OSM_TAG_COLOURS, SAC_SCALE_COLOURS


def add_gpx(traces: list[go.Scattermap], route_gdf: gpd.GeoDataFrame) -> None:
    ''' Add points of GPX trajectory from route_gdf to traces list for later plotting. '''
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


def add_graph(traces: list[go.Scattermap], osm_graph: networkx.MultiDiGraph) -> None:
    ''' Add points of osm_graph to traces list for later plotting. '''
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
            edge_lons.extend([float(x1), float(x2), None]) # Add edge coordinates and None to separate edges when plotting
            edge_lats.extend([float(y1), float(y2), None]) # Add edge coordinates and None to separate edges when plotting

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


def add_matched_edges(traces: list[go.Scattermap], matched_edges: gpd.GeoDataFrame, selected_tag: str, selected_values: list[str]) -> None:
    ''' Process matched OSM edges for plotting. '''
    # Show only matched edges with the selected OSM tag
    if selected_tag != "None" and selected_tag in matched_edges.columns:
        matched_edges = matched_edges[
            matched_edges[selected_tag].notna()
        ]

        # Show only matched edges with the selected OSM tag value
        if selected_values:
            matched_edges = matched_edges[
                matched_edges[selected_tag].astype(str).isin(
                    selected_values
                )
            ]

    # Convert matched edges to longitude/latitude for plotting
    matched_edges = matched_edges.to_crs("EPSG:4326")

    matched_lons = []
    matched_lats = []

    for geometry in matched_edges.geometry:
        if geometry is not None:
            if geometry.geom_type == "LineString":
                lons, lats = geometry.xy
                matched_lons.extend([*lons, None]) # Add edge coordinates and None to separate edges when plotting
                matched_lats.extend([*lats, None]) # Add edge coordinates and None to separate edges when plotting

    if selected_values and selected_tag != "None": # If both OSM tag and OSM tag value are selected
        for i, value in enumerate(selected_values):
            value_edges = matched_edges[
                matched_edges[selected_tag].astype(str) == value
            ]

            value_lons = []
            value_lats = []

            for geometry in value_edges.geometry:
                if geometry is not None and geometry.geom_type == "LineString":
                    lons, lats = geometry.xy
                    value_lons.extend([*lons, None]) # Add edge coordinates and None to separate edges when plotting
                    value_lats.extend([*lats, None]) # Add edge coordinates and None to separate edges when plotting

            if value_lons:
                if selected_tag == "sac_scale":
                    display_value = format_sac_scale(value) # Special treatment of OSM tags for sac_scale to add SAC codes
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
                                SAC_SCALE_COLOURS.get(value)
                                if selected_tag == "sac_scale"
                                else OSM_TAG_COLOURS[i % len(OSM_TAG_COLOURS)]
                            ),
                        ),
                        hoverinfo="skip",
                    )
                )

    elif matched_lons: # If only OSM tag is selected
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


def render_map(traces: list[go.Scattermap], route_gdf: gpd.GeoDataFrame) -> None:
    ''' Apply settings for plotting of GPX trajectory, OSM graph and satellite raster image.'''
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
            style="white-bg", # Empty white canvas which results in no external HTTP requests at this point (https://plotly.com/python/tile-map-layers/) (satellite layer is added later below)
            center=dict(
                lon=center_lon,
                lat=center_lat,
            ),
            zoom=zoom,
            layers=[
                dict(
                    below="traces",
                    sourcetype="raster", # Fetch pre-rendered map tiles
                    source=[
                        "https://server.arcgisonline.com/ArcGIS/rest/services/"
                        "World_Imagery/MapServer/tile/{z}/{y}/{x}" # {z} represents the zoom level, {x} represents the column, and {y} represents the row of the tile (https://openlayers.org/en/latest/examples/xyz-esri.html)
                        # Standard web tiling system Popular Visualisation Pseudo Mercator (EPSG:3857) (https://spatialreference.org/ref/epsg/3857/)
                    ],
                )
            ],
        ),
    )

    st.subheader("GPX and OSM graph")
    # Actually plot fig
    st.plotly_chart(
        fig,
        width="stretch", # To use entire width of screens that are larger than laptop screens
        config={"displaylogo": False},
    )


