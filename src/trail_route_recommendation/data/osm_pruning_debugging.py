import geopandas as gpd
import osmnx as ox
import networkx as nx
import ast
import time
from pathlib import Path
from datetime import datetime

from shapely.geometry import Point

SAC_SCALE_CODES = {
    "hiking": "T1",
    "mountain_hiking": "T2",
    "demanding_mountain_hiking": "T3",
    "alpine_hiking": "T4",
    "demanding_alpine_hiking": "T5",
    "difficult_alpine_hiking": "T6",
}


def normalize_osm_tag_value(value: object) -> object:
    ''' Pre-process OSM tag value because there might be multi-valued tags that have comma-separated lists or strings as values. '''
    if isinstance(value, list):
        return str(sorted(value))

    if isinstance(value, str) and value.startswith("["):
        try:
            values = ast.literal_eval(value)

            if isinstance(values, list):
                return str(sorted(values))
        except (ValueError, SyntaxError):
            pass

    return value


def format_sac_scale(value: str) -> str:
    # Account for multiple comma-separated tag value on OSM edges
    try:
        if value.startswith("["):
            values = ast.literal_eval(value)
        else:
            values = [value]
    except (ValueError, SyntaxError):
        values = [value]

    values = sorted(values)

    display_values = []

    for item in values:
        display_values.append(f"{SAC_SCALE_CODES.get(item, item)} — {item}") # Code comes first to enable alphabetical sorting for this ordinal feature

    if len(display_values) > 1:
        return f"[{', '.join(display_values)}]" # There might be multi-valued tags that have comma-separated lists or strings as values
    else:
        return display_values[0]


def project_to_metric_crs(
    gpx: gpd.GeoDataFrame,
    graph: nx.MultiDiGraph,
) -> tuple[gpd.GeoDataFrame, nx.MultiDiGraph]:
    ''' Project GPX points and OSM graph to a suitable metric CRS. UoM is metres for later distance calculation in GPX <> OSM matching. This CRS is projected on a 2D plane (not with latitude, longitude). '''

    target_crs = gpx.estimate_utm_crs()

    gpx_projected = gpx.to_crs(target_crs)
    graph_projected = ox.project_graph(graph, to_crs=target_crs)

    return gpx_projected, graph_projected


def match_gpx_to_edges(
    gpx_points: gpd.GeoDataFrame,
    osm_graph: nx.MultiDiGraph,
    bad_match_threshold_m: float = 100,
) -> gpd.GeoDataFrame:
    ''' Match GPX points to their nearest OSM edges within the distance threshold. '''

    # Project GPX points and graph to a suitable metric CRS for distance calculation in metres
    gpx_projected, graph_projected = project_to_metric_crs(
        gpx_points,
        osm_graph,
    )
    target_crs = gpx_projected.crs

    # Get GPX point coordinates and convert to numpy arrays for efficiency
    x = gpx_projected.geometry.x.to_numpy()
    y = gpx_projected.geometry.y.to_numpy()

    # Find nearest OSM graph edge for each GPX point
    matched_edge_ids = ox.distance.nearest_edges(
        graph_projected,
        X=x,
        Y=y,
    )

    # Convert OSM graph edges to GeoDataFrame for data processing. Nodes are omitted because we are only interested in edge tags.
    edges = ox.graph_to_gdfs(
        graph_projected,
        nodes=False,
        edges=True,
    )

    valid_edge_ids = set()

    # Passing GPX point/OSM edge pairs via zip() function
    for point, edge_id in zip(
        gpx_projected.geometry,
        matched_edge_ids,
    ):
        edge_id = tuple(edge_id)
        edge_geometry = edges.loc[edge_id].geometry

        distance = point.distance(edge_geometry) # Determine distance between GPX point and nearest/matched OSM edge

        if distance <= bad_match_threshold_m: # Keep only matches within 100 m
            valid_edge_ids.add(edge_id)

    # Select valid matched edges
    matched_edges = edges.loc[
        edges.index.isin(valid_edge_ids)
    ].copy()

    # Sort multi-value OSM tags such that equivalent combinations have one representation
    for column in matched_edges.columns:
        if column != "geometry":
            matched_edges[column] = matched_edges[column].apply(
                normalize_osm_tag_value
            )

    # Store the already projected GPX geometry and CRS for the deadend pruning step
    matched_edges.attrs["gpx_geometry"] = (
        gpx_projected.geometry.union_all() # Perform union in case of overlapping GPX segments
    )
    matched_edges.attrs["target_crs"] = target_crs
    matched_edges.attrs["gpx_crs"] = gpx_projected.crs

    return matched_edges


def remove_short_deadends(
    matched_edges: gpd.GeoDataFrame,
    osm_graph: nx.MultiDiGraph,
    protected_nodes: set[int],
    max_edges: int = 3,
    min_gpx_distance_m: float = 15,
) -> gpd.GeoDataFrame:

    total_start = time.perf_counter()

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "deadend_debug.log"

    debug_lines = []

    def debug(message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        debug_lines.append(f"{timestamp} | {message}")

    debug("START DEADEND PRUNING")
    debug(f"Matched edges: {len(matched_edges)}")
    debug(f"Protected nodes: {len(protected_nodes)}")
    debug(f"Minimum GPX distance for protected deadends: {min_gpx_distance_m} m")

    # Construct OSM graph with matched_edges and all nodes incident to matched_edges
    matched_graph = osm_graph.edge_subgraph(
        matched_edges.index
    ).copy()

    gpx_geometry = matched_edges.attrs.get("gpx_geometry")
    target_crs = matched_edges.attrs.get("target_crs")

    if gpx_geometry is None or target_crs is None:
        raise ValueError(
            "GPX geometry is missing from matched_edges."
            "Use match_gpx_to_edges() before remove_short_deadends()."
        )

    debug(
        f"Matched graph: "
        f"{matched_graph.number_of_nodes()} nodes, "
        f"{matched_graph.number_of_edges()} edges"
    )

    # Convert OSM graph to undirected graph to simplify degree calculation
    undirected_graph = matched_graph.to_undirected()

    deadend_nodes = []
    for node in matched_graph.nodes:
        if undirected_graph.degree(node) == 1 and node not in protected_nodes: # If degree = 1 for a node is an undirected graph, then this is considered a deadend node candidate
            deadend_nodes.append(node)

    debug(
        f"Found {len(deadend_nodes)} deadend nodes"
    )

    edges_to_remove = set()

    for deadend_node in deadend_nodes:

        node_data = matched_graph.nodes[deadend_node]

        deadend_point = Point(
            node_data["x"],
            node_data["y"],
        )

        debug(
            f"GRAPH CRS: {matched_graph.graph.get('crs')}"
        )
        debug(
            f"TARGET CRS: {target_crs}"
        )
        debug(
            f"END NODE RAW: x={node_data['x']}, y={node_data['y']}"
        )

        graph_crs = matched_graph.graph.get("crs")

        if graph_crs is None:
            raise ValueError(
                "OSM graph CRS is missing. Cannot transform deadend node to target CRS."
            )

        # Convert deadend point to metric, 2D CRS
        deadend_point_projected = gpd.GeoSeries(
            [deadend_point],
            crs=graph_crs,
        ).to_crs(target_crs).iloc[0]

        debug(
            f"END NODE TRANSFORMED: x={deadend_point_projected.x}, "
            f"y={deadend_point_projected.y}"
        )

        # Calculate max. distance between OSM deadend node and GPX trajectory
        max_gpx_distance = deadend_point_projected.distance(gpx_geometry)

        debug(
            f"END NODE DISTANCE TO GPX: {max_gpx_distance:.2f} m"
        )

        debug(
            f"DEADEND CANDIDATE: node={deadend_node}, "
            f"degree={undirected_graph.degree(deadend_node)}, "
            f"gpx_distance={max_gpx_distance:.2f} m, "
            f"threshold={min_gpx_distance_m:.2f} m"
        )

        if max_gpx_distance <= min_gpx_distance_m: # Small deviations between OSM network and GPX trajectory are accepted
            debug(
                f"KEEP DEADEND: node={deadend_node} because "
                f"distance to GPX trajectory {max_gpx_distance:.2f} m <= "
                f"{min_gpx_distance_m:.2f} m"
            )
            continue

        current_node = deadend_node
        branch = []
        visited_nodes = {current_node}

        for _ in range(max_edges):

            if current_node in protected_nodes:
                break

            neighbours = list(
                undirected_graph.neighbors(current_node)
            )

            # If there are 0 or > 1 neighbours, then the node is not a genuine deadend node
            if len(neighbours) != 1:
                break

            next_node = neighbours[0]

            if next_node in visited_nodes:
                break

            visited_nodes.add(next_node)

            # Check both directions because the OSM edge may be directed either way

            # Direction 1
            edge_data = matched_graph.get_edge_data(
                current_node,
                next_node,
            )

            if edge_data:
                edge = (
                    current_node,
                    next_node,
                    next(iter(edge_data)),
                )

            else:
                # Direction 2
                edge_data = matched_graph.get_edge_data(
                    next_node,
                    current_node,
                )

                if not edge_data:
                    break

                edge = (
                    next_node,
                    current_node,
                    next(iter(edge_data)),
                )

            branch.append(edge) # Build the deadend branch
            current_node = next_node

        final_degree = undirected_graph.degree(current_node)

        if (
            len(branch) <= max_edges # We're only removing short deadend branches
            and current_node not in protected_nodes
            and final_degree >= 1 # Check if node is still connected to OSM graph
        ):
            edges_to_remove.update(branch)
            debug(
                f"PRUNE BRANCH: start_node={deadend_node}, "
                f"edges={branch}, final_node={current_node}, "
                f"final_degree={final_degree}"
            )
        else:
            debug(
                f"KEEP BRANCH: start_node={deadend_node}, "
                f"edges={branch}, final_node={current_node}, "
                f"final_degree={final_degree}, "
                f"final_protected={current_node in protected_nodes}"
            )

    matched_graph.remove_edges_from(edges_to_remove)

    debug(
        f"Removed {len(edges_to_remove)} edges, "
        f"{matched_graph.number_of_edges()} remain"
    )

    debug(
        f"DEADEND PRUNING FINISHED: "
        f"{matched_graph.number_of_edges()} edges remain"
    )
    debug(
        f"Total pruning time: "
        f"{time.perf_counter() - total_start:.3f} s"
    )

    with log_file.open("a", encoding="utf-8") as f:
        f.write("\n".join(debug_lines) + "\n")

    # Extract the edges that are left after deadend pruning
    remaining_edge_ids = set(
        matched_graph.edges(keys=True)
    )

    remaining_edges = matched_edges.loc[
        matched_edges.index.isin(remaining_edge_ids)
    ].copy()

    return remaining_edges


def get_protected_nodes(
    gpx_points: gpd.GeoDataFrame,
    osm_graph: nx.MultiDiGraph,
) -> set[int]:
    ''' Identify the OSM nodes that are the start and end of the GPX route, which are protected during deadend removal. '''

    # Project GPX points and graph to a suitable metric CRS such that ox.distance.nearest_edges() can be applied later
    gpx_projected, graph_projected = project_to_metric_crs(
        gpx_points,
        osm_graph,
    )

    start_point = gpx_projected.geometry.iloc[0]
    end_point = gpx_projected.geometry.iloc[-1]

    start_edge = ox.distance.nearest_edges(
        graph_projected,
        X=[start_point.x],
        Y=[start_point.y],
    )[0]

    end_edge = ox.distance.nearest_edges(
        graph_projected,
        X=[end_point.x],
        Y=[end_point.y],
    )[0]

    protected_nodes = {
        start_edge[0],
        start_edge[1],
        end_edge[0],
        end_edge[1],
    }

    return protected_nodes
