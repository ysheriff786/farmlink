import json
import math
import urllib.error
import urllib.request

OSRM_BASE = "http://router.project-osrm.org"
USE_OSRM = True


def osrm_table_request(coords):
    """Get a distance/duration matrix from OSRM.

    Args:
        coords: List of (lat, lng) tuples

    Returns:
        dict with distances (km) and durations (minutes) matrices, or None on failure.
    """
    if not coords or len(coords) < 2:
        return None

    coord_str = ";".join(f"{lng},{lat}" for lat, lng in coords)
    url = f"{OSRM_BASE}/table/v1/driving/{coord_str}?annotations=distance,duration"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FarmLink/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("code") == "Ok":
            return {
                "distances": [[d / 1000 for d in row] for row in data["distances"]],
                "durations": [[d / 60 for d in row] for row in data["durations"]],
            }
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
        pass
    return None


def osrm_route_request(coords):
    """Get an optimized route from OSRM.

    Args:
        coords: List of (lat, lng) tuples in order

    Returns:
        dict with total_distance_km, total_duration_min, geometry, or None on failure.
    """
    if not coords or len(coords) < 2:
        return None

    coord_str = ";".join(f"{lng},{lat}" for lat, lng in coords)
    url = f"{OSRM_BASE}/route/v1/driving/{coord_str}?overview=full&geometries=geojson"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FarmLink/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            return {
                "total_distance_km": round(route["distance"] / 1000, 2),
                "total_duration_min": round(route["duration"] / 60, 1),
                "geometry": route.get("geometry"),
            }
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
        pass
    return None


def calculate_distance_matrix(coords):
    """Calculate distance matrix. Tries OSRM first, falls back to Haversine.

    Args:
        coords: List of (lat, lng) tuples

    Returns:
        dict with distances (km) and durations (minutes) matrices
    """
    if USE_OSRM:
        result = osrm_table_request(coords)
        if result:
            return result

    n = len(coords)
    distances = [[0.0] * n for _ in range(n)]
    durations = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i != j:
                d = haversine(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                distances[i][j] = d
                durations[i][j] = round(d / 40 * 60, 1)

    return {"distances": distances, "durations": durations}


def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two points."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def nearest_neighbor_tsp(dist_matrix, start=0):
    """Solve TSP using nearest-neighbor heuristic.

    Args:
        dist_matrix: 2D list of distances
        start: Starting index

    Returns:
        List of indices representing the tour order
    """
    n = len(dist_matrix)
    if n <= 1:
        return list(range(n))

    visited = {start}
    tour = [start]
    current = start

    while len(visited) < n:
        best_next = None
        best_dist = float("inf")
        for j in range(n):
            if j not in visited and dist_matrix[current][j] < best_dist:
                best_dist = dist_matrix[current][j]
                best_next = j
        if best_next is not None:
            visited.add(best_next)
            tour.append(best_next)
            current = best_next

    return tour


def optimize_delivery_route(pickups, deliveries, depot=None):
    """Optimize a delivery route with pickup-before-delivery constraints.

    Uses OSRM if available, falls back to nearest-neighbor heuristic.

    Args:
        pickups: List of dicts with lat, lng, id, load_kg
        deliveries: List of dicts with lat, lng, id, load_kg
        depot: Optional (lat, lng) starting point

    Returns:
        dict with optimized_stop_order, total_distance_km, total_duration_min,
              total_load_kg, stops, route_geometry
    """
    if not pickups and not deliveries:
        return {
            "optimized_stop_order": [],
            "total_distance_km": 0,
            "total_duration_min": 0,
            "total_load_kg": 0,
            "stops": [],
            "route_geometry": None,
        }

    all_stops = []
    coords = []

    if depot:
        all_stops.append({
            "type": "depot", "lat": depot[0], "lng": depot[1],
            "id": 0, "load_kg": 0, "label": "Start/End"
        })
        coords.append(depot)

    for p in pickups:
        all_stops.append({
            "type": "pickup", "lat": p["lat"], "lng": p["lng"],
            "id": p.get("id", 0), "load_kg": p.get("load_kg", 0),
            "label": f"Pickup #{p.get('id', '')}"
        })
        coords.append((p["lat"], p["lng"]))

    for d in deliveries:
        all_stops.append({
            "type": "delivery", "lat": d["lat"], "lng": d["lng"],
            "id": d.get("id", 0), "load_kg": d.get("load_kg", 0),
            "label": f"Delivery #{d.get('id', '')}"
        })
        coords.append((d["lat"], d["lng"]))

    if len(coords) < 2:
        return {
            "optimized_stop_order": list(range(len(all_stops))),
            "total_distance_km": 0,
            "total_duration_min": 0,
            "total_load_kg": sum(s["load_kg"] for s in all_stops),
            "stops": all_stops,
            "route_geometry": None,
        }

    dm = calculate_distance_matrix(coords)
    distances = dm["distances"]

    start_idx = 0
    depot_offset = 1 if depot else 0
    pickup_indices = list(range(depot_offset, depot_offset + len(pickups)))
    delivery_indices = list(range(depot_offset + len(pickups), len(all_stops)))

    tour = nearest_neighbor_tsp(distances, start_idx)

    # Enforce pickup-before-delivery: walk the NN tour in order, emitting
    # pickups immediately and holding deliveries until every pickup is done.
    constrained_tour = []
    all_pickups = set(pickup_indices)
    all_deliveries = set(delivery_indices)
    held_deliveries = []

    for idx in tour:
        if idx in all_pickups:
            constrained_tour.append(idx)
            all_pickups.discard(idx)
        elif idx in all_deliveries:
            all_deliveries.discard(idx)
            held_deliveries.append(idx)

    # Once all pickups are done, flush held deliveries in tour order.
    constrained_tour.extend(held_deliveries)

    # If we get here without visiting all stops (defensive), append leftovers.
    constrained_tour.extend(sorted(all_pickups))
    constrained_tour.extend(sorted(all_deliveries))

    total_dist = 0
    total_dur = 0
    for i in range(len(constrained_tour) - 1):
        total_dist += distances[constrained_tour[i]][constrained_tour[i + 1]]
        total_dur += dm["durations"][constrained_tour[i]][constrained_tour[i + 1]]

    if depot and len(constrained_tour) > 0:
        total_dist += distances[constrained_tour[-1]][0]
        total_dur += dm["durations"][constrained_tour[-1]][0]

    route_coords = [(all_stops[i]["lat"], all_stops[i]["lng"]) for i in constrained_tour]
    if depot:
        route_coords.insert(0, depot)
        route_coords.append(depot)

    geometry = None
    if USE_OSRM and len(route_coords) >= 2:
        osrm_result = osrm_route_request(route_coords)
        if osrm_result:
            total_dist = osrm_result["total_distance_km"]
            total_dur = osrm_result["total_duration_min"]
            geometry = osrm_result.get("geometry")

    optimized_stops = []
    cumulative_load = 0
    for i, stop_idx in enumerate(constrained_tour):
        stop = all_stops[stop_idx].copy()
        stop["sequence"] = i + 1
        if stop["type"] == "pickup":
            cumulative_load += stop["load_kg"]
        elif stop["type"] == "delivery":
            cumulative_load -= stop["load_kg"]
        stop["cumulative_load_kg"] = max(0, cumulative_load)
        optimized_stops.append(stop)

    return {
        "optimized_stop_order": constrained_tour,
        "total_distance_km": round(total_dist, 2),
        "total_duration_min": round(total_dur, 1),
        "total_load_kg": sum(p.get("load_kg", 0) for p in pickups),
        "stops": optimized_stops,
        "route_geometry": geometry,
    }


def calculate_eta(distance_km, avg_speed_kmh=40):
    """Calculate estimated time of arrival.

    Args:
        distance_km: Distance in kilometers
        avg_speed_kmh: Average speed in km/h (default 40 for mixed roads)

    Returns:
        Duration in minutes
    """
    if distance_km <= 0:
        return 0
    return round(distance_km / avg_speed_kmh * 60, 1)


def validate_vehicle_capacity(vehicle_capacity_kg, total_load_kg):
    """Validate that vehicle can handle the load.

    Returns:
        (is_valid, message)
    """
    if vehicle_capacity_kg <= 0:
        return False, "Vehicle capacity not set"
    if total_load_kg > vehicle_capacity_kg:
        return False, f"Load ({total_load_kg:.1f} kg) exceeds vehicle capacity ({vehicle_capacity_kg:.1f} kg)"
    utilization = round(total_load_kg / vehicle_capacity_kg * 100, 1)
    return True, f"Capacity OK ({utilization}% utilization)"
