import random
import ast
import math
import osmnx as ox

# Define how OSM tags are aggregated into route features
OSM_FEATURES = {
    "osm_trail_share": ("highway", ["path", "footway", "track", "steps"]),
    "osm_paved_share": (
        "surface",
        [
            "paved",
            "asphalt",
            "concrete",
            "concrete:plates",
            "concrete:lanes",
            "paving_stones",
            "sett",
            "cobblestone",
        ],
    ),
    "osm_sac_t3plus_share": (
        "sac_scale",
        [
            "demanding_mountain_hiking",
            "alpine_hiking",
            "demanding_alpine_hiking",
            "difficult_alpine_hiking",
        ],
    ),
    "osm_track_grade3plus_share": (
        "tracktype",
        ["grade3", "grade4", "grade5"],
    ),
}


def osm_features(edge_data, lengths):
    """Calculate length-weighted OSM tag shares, keeping missing tags explicit."""
    # Use total route length as basis for all OSM feature shares
    total = sum(lengths)

    if total <= 0:
        raise ValueError("OSM features require a positive route length.")

    features = {}

    # Calculate each configured OSM feature separately
    for name, (tag, accepted) in OSM_FEATURES.items():
        matched = 0.0
        missing = 0.0

        for edge, length in zip(edge_data, lengths):
            value = edge.get(tag)

            # Convert OSM values stored as string representations of lists back to Python lists for later processing
            if isinstance(value, str) and value.startswith("["):
                try:
                    value = ast.literal_eval(value)
                except (ValueError, SyntaxError):
                    value = None

            # Treat a single OSM tag value the same way as a list with one value. This is relevant because we want that value is a list in any case
            if not isinstance(value, (list, tuple)):
                value = [value]

            values = []

            for item in value:
                if item is not None and str(item) not in ("", "nan", "None"): # Hedge against any strange tag values that express a missing value
                    values.append(str(item))

            if not values:
                missing += length # Add edge length to the missing distance collector
                continue

            count = 0

            for item in values:
                if item in accepted:
                    count += 1

            # Split section length betweenn multiple tag values on the same OSM edge. Relevant is not all tag values are accepted, for example if an edge has values path and service for "highway" (only path is in OSM_FEATURES)
            matched += length * count / len(values)

        features[name] = matched / total
        features["osm_" + tag + "_missing_share"] = missing / total

    return features


def route_features(graph, route, edges=None):
    """Calculate the features of a complete route."""
    # The route is identified by node IDs. OSMnx returns the corresponding path sections.
    if edges is None: # The search can pass the edges to avoid loading them twice, but edges can be extracted from OSM graph
        edges = ox.routing.route_to_gdf(graph, route, weight="length")

    distance_m = 0.0
    for edge_length in edges["length"]:
        distance_m = distance_m + edge_length

    features = {}
    features["distance_m"] = distance_m

    # Add ascent, slope distribution, climbs, surface, etc. here later.
    return features


# Distribute feature group weights across the active features in each group
def get_grouped_weights(reference, weights, group_weights):
    # WorldCover features belong to one common feature group
    worldcover_names = [
        "tree_cover",
        "shrubland",
        "grassland",
        "cropland",
        "built_up",
        "bare_sparse_vegetation",
        "snow_ice",
        "permanent_water",
        "herbaceous_wetland",
        "mangroves",
        "moss_lichen",
    ]

    # Keep track of total individual feature weight within each feature group
    group_totals = {
        "distance": 0.0,
        "dem": 0.0,
        "worldcover": 0.0,
        "osm": 0.0,
    }

    # Check that all provided feature group weights are valid
    for group in group_weights:
        if group not in group_totals:
            raise ValueError("Unknown feature group: " + group)

        group_weight = float(group_weights[group])

        if not math.isfinite(group_weight) or group_weight < 0:
            raise ValueError("Invalid group weight: " + group)

    if weights is None:
        weights = {}

        for name in reference:
            weights[name] = 1.0 # Use equal individual feature weights if no specific weights were provided

    feature_groups = {}

    for name in weights:
        weight = float(weights[name])

        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Invalid weight for feature: " + name)

        if name == "distance_m":
            group = "distance"
        elif name.startswith("osm_"):
            group = "osm"
        elif name in worldcover_names:
            group = "worldcover"
        else:
            group = "dem"

        feature_groups[name] = group
        group_totals[group] += weight

    # Calculate final feature weights while preserving the requested group weights
    grouped_weights = {}

    # Assign every feature to its corresponding feature group
    for name in weights:
        group = feature_groups[name]
        grouped_weights[name] = 0.0

        if group_totals[group] > 0:
            group_weight = float(group_weights.get(group, 1.0))
            grouped_weights[name] = (
                group_weight * float(weights[name]) / group_totals[group]
            )

    return grouped_weights


# Compare candidate and reference route features and return one overall error value
def similarity_error(features, reference, weights=None, scales=None, group_weights=None):
    """Return the weighted mean of scaled feature differences; smaller is better."""
    # Convert feature group weights into weights for the individual features
    if group_weights is not None:
        weights = get_grouped_weights(reference, weights, group_weights)

    # Use equal individual feature weights if no specific weights were provided
    if weights is None:
        weights = {}

        for name in reference:
            weights[name] = 1.0

    if scales is None:
        scales = {}

    # Accumulate scaled feature differences according to their weights
    weighted_error = 0.0
    total_weight = 0.0

    # Assign every feature to its feature group
    for name in weights:
        weight = float(weights[name])

        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Invalid weight for feature: " + name)

        if weight == 0:
            continue

        if name not in features or name not in reference: # Hedge for edge case
            raise ValueError("Missing candidate or reference feature: " + name)

        value = float(features[name])
        target = float(reference[name])

        if not math.isfinite(value) or not math.isfinite(target):
            raise ValueError("Invalid candidate or reference feature: " + name)

        scale = float(scales.get(name, max(abs(target), 1.0))) # Take provided scale if possible. Otherwise, take scale of reference but min. 1.0

        if scale == 0:
            continue  # Skip feature because otherwise we would divide by 0 later

        if not math.isfinite(scale) or scale < 0: # Hedge for edge case
            raise ValueError("Invalid scale for feature: " + name)

        # Calculate scaled absolute difference between candidate and reference feature
        difference = abs(value - target) / scale
        weighted_error += weight * difference
        total_weight += weight

    if total_weight == 0:
        raise ValueError("At least one feature must have a positive weight greater than 0.")

    return weighted_error / total_weight # Essentially a weighted mean is calculated


def calculate_repeat_fraction(edges):
    # Count extra visits to the same path, also when running back the other way (OSM graph is directed)
    seen_edges = set() # Use set because it is not considered how often an edge is visited repeatedly, but often **if** it is visited repeatedly
    total_distance = 0.0
    repeated_distance = 0.0
    for edge_id, edge in edges.iterrows(): # Iterate through all edges in graph
        u, v, key = edge_id
        coordinates = tuple(edge.geometry.coords)
        reverse_coordinates = tuple(reversed(coordinates))
        if reverse_coordinates < coordinates:
            coordinates = reverse_coordinates # Use the same order for both directions
        osm_ids = edge.get("osmid", [])
        if not isinstance(osm_ids, list): # Making osm_ids iterable
            osm_ids = [osm_ids]
        osm_id_strings = []
        for osm_id in osm_ids:
            osm_id_strings.append(str(osm_id))
        osm_id_strings.sort()
        # Two different paths can connect the same nodes, so also check their shape and OSM IDs for the clear path identifier
        path_id = (min(u, v), max(u, v), tuple(osm_id_strings), coordinates)
        length = float(edge["length"])
        total_distance = total_distance + length
        if path_id in seen_edges:
            repeated_distance = repeated_distance + length
        else:
            seen_edges.add(path_id)
    if total_distance == 0:
        return 0.0
    return repeated_distance / total_distance


def get_error(candidate):
    """This gettter function returns the value that is used for sorting candidates later."""
    return candidate.get("total_error", candidate["error"]) # Include the penalty when sorting


def generate_routes(graph, start, reference, attempts=100, top_k=3, seed=42,
                    distance_range=None):
    """Generate several routes and return the best ones."""
    if reference["distance_m"] <= 0:
        raise ValueError("Reference distance must be positive.")

    minimum_distance = reference["distance_m"] * 0.80
    maximum_distance = reference["distance_m"] * 1.20
    if distance_range is not None:
        minimum_distance, maximum_distance = distance_range # Both values are in metres
    if (not math.isfinite(minimum_distance) or not math.isfinite(maximum_distance)
            or minimum_distance <= 0 or maximum_distance < minimum_distance):
        raise ValueError("Use a positive minimum distance and a maximum at least as large.")
    reference = reference.copy() # Keep the original reference unchanged
    reference["distance_m"] = (minimum_distance + maximum_distance) / 2

    # Using the same seed makes the random selection reproducible.
    random_generator = random.Random(seed)

    # All nodes from the OSM graph, except the start, can serve as waypoints.
    possible_waypoint_nodes = []
    for node in graph.nodes:
        if node != start:
            possible_waypoint_nodes.append(node)

    candidates = []
    seen_routes = []

    if len(possible_waypoint_nodes) < 2:
        return candidates

    for _ in range(attempts):
        # Randomly select two distinct waypoints.
        waypoints = random_generator.sample(possible_waypoint_nodes, 2)
        first_waypoint = waypoints[0]
        second_waypoint = waypoints[1]

        # When having two waypoints, three consecutive shortest paths together form a closed route.
        first_leg = ox.routing.shortest_path(graph, start, first_waypoint, weight="length")
        second_leg = ox.routing.shortest_path(graph, first_waypoint, second_waypoint, weight="length")
        third_leg = ox.routing.shortest_path(graph, second_waypoint, start, weight="length")

        # If a connection is missing, this means that the route is not closed --> Continue with the next attempt.
        if first_leg is None or second_leg is None or third_leg is None:
            continue

        # Build the route trajectory that is composed by the three legs.
        route = list(first_leg)

        # The first node of the next leg is already included because end of each leg is identical with start of next leg.
        # Therefore, start at index 1 and append the remaining nodes each time.
        for index in range(1, len(second_leg)):
            route.append(second_leg[index])

        for index in range(1, len(third_leg)):
            route.append(third_leg[index])

        # Do not evaluate the exact same node sequence more than once.
        if route in seen_routes:
            continue
        seen_routes.append(route)

        edges = ox.routing.route_to_gdf(graph, route, weight="length")
        features = route_features(graph, route, edges) # Reuse these edges for the repetition check below

        # Keep only routes inside the chosen distance range
        if features["distance_m"] < minimum_distance or features["distance_m"] > maximum_distance:
            continue

        candidate = {}
        candidate["nodes"] = route
        candidate["features"] = features
        candidate["error"] = similarity_error(features, reference)
        candidate["repeat_fraction"] = calculate_repeat_fraction(edges)
        candidate["repeat_penalty"] = max(0.0, candidate["repeat_fraction"] - 0.10) # 10% repetition are allowed because especially in trail running short out-and-back segments are ok, for example to reach summits
        candidate["total_error"] = candidate["error"] + candidate["repeat_penalty"]
        candidates.append(candidate)

    # Sort by distance error plus repetition penalty and select max. top_k routes for displaying in Streamlit app
    candidates.sort(key=get_error)
    best_routes = []
    for candidate in candidates:
        if len(best_routes) >= top_k:
            break
        best_routes.append(candidate)

    return best_routes


def route_to_gpx(graph, route):
    """Export a route from a longitude/latitude CRS graph, including edge geometry."""
    import gpxpy.gpx

    # Create a GPX file with one track and one segment (GPX file is tructured hierarchically)
    gpx = gpxpy.gpx.GPX() # Empty GPX file
    track = gpxpy.gpx.GPXTrack() # Empty GPX file track
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment() # Empty GPX file track segment
    track.segments.append(segment)
    # Include bends in the paths between the nodes
    edges = ox.routing.route_to_gdf(graph, route, weight="length")
    # Remember the previous point to avoid adding it twice
    previous = None
    for index, geometry in enumerate(edges.geometry):
        coordinates = list(geometry.coords)
        node = graph.nodes[route[index]]
        # Check which end is closer to the start of this path
        first_distance = (coordinates[0][0] - node["x"]) ** 2 + (coordinates[0][1] - node["y"]) ** 2
        last_distance = (coordinates[-1][0] - node["x"]) ** 2 + (coordinates[-1][1] - node["y"]) ** 2
        # Some edge geometries are stored in the opposite direction, we need to handle that corner case
        if last_distance < first_distance:
            coordinates.reverse()
        for coordinate in coordinates:
            if coordinate != previous:
                segment.points.append(gpxpy.gpx.GPXTrackPoint(coordinate[1], coordinate[0])) # GPX needs latitude first, then longitude
                previous = coordinate
    # Returning file in XML format contents for the GPX download
    return gpx.to_xml().encode("utf-8")
