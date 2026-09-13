import random
import osmnx as ox


def route_features(graph, route):
    """Calculate the features of a complete route."""
    # The route is identified by node IDs. OSMnx returns the corresponding path sections.
    edges = ox.routing.route_to_gdf(graph, route, weight="length")

    distance_m = 0.0
    for edge_length in edges["length"]:
        distance_m = distance_m + edge_length

    features = {}
    features["distance_m"] = distance_m

    # Add ascent, slope distribution, climbs, surface, etc. here later.
    return features


def similarity_error(features, reference):
    """Calculate the deviation from the reference. A smaller value is better."""
    route_distance = features["distance_m"]
    reference_distance = reference["distance_m"]

    difference = abs(route_distance - reference_distance)
    relative_error = difference / reference_distance

    # For now, we only compare distance. 0.10 means a 10% deviation.
    # Add the weighted comparison of all agreed features here later.
    return relative_error


def get_error(candidate):
    """This gettter function returns the value that is used for sorting candidates later."""
    return candidate["error"]


def generate_routes(graph, start, reference, attempts=100, top_k=3, seed=42):
    """Generate several routes and return the best ones."""
    if reference["distance_m"] <= 0:
        raise ValueError("Reference distance must be positive.")

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

        features = route_features(graph, route)

        # Only keep routes with a distance deviation of at most 20%.
        distance_difference = abs(features["distance_m"] - reference["distance_m"])
        relative_difference = distance_difference / reference["distance_m"]
        if relative_difference > 0.20:
            continue

        candidate = {}
        candidate["nodes"] = route
        candidate["features"] = features
        candidate["error"] = similarity_error(features, reference)
        candidates.append(candidate)

    # Sort by deviation in ascending order and select max. top_k routes.
    candidates.sort(key=get_error)
    best_routes = []
    for candidate in candidates:
        if len(best_routes) >= top_k:
            break
        best_routes.append(candidate)

    return best_routes

if __name__ == "__main__":
    # Example location in Innsbruck: latitude and longitude.
    latitude = 47.2692
    longitude = 11.4041
    location = (latitude, longitude)

    # Download a walking network from OSM. This requires an internet connection since the overpass API is queried.
    graph = ox.graph_from_point(location, dist=4000, network_type="walk")

    # Alternatively, replace the line above by loading an existing graph:
    # graph = ox.load_graphml("area.graphml")

    # Use the nearest network node (from given location) as the start.
    start = ox.distance.nearest_nodes(graph, X=longitude, Y=latitude)

    # Example value. Insert the features of the entire reference race here later.
    reference = {}
    reference["distance_m"] = 10000

    routes = generate_routes(graph, start, reference)

    route_number = 1
    for route in routes:
        print("route:", route_number)
        print("features:", route["features"])
        print("relative error:", route["error"])
        route_number = route_number + 1

    if len(routes) == 0:
        print("No route found; adjust search area and/or number of attempts.")
