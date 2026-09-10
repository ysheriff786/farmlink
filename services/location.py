import math

from sqlalchemy import text

EARTH_RADIUS_KM = 6371.0


def haversine(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points on Earth."""
    if not all(isinstance(v, (int, float)) for v in [lat1, lon1, lat2, lon2]):
        return 0.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return round(EARTH_RADIUS_KM * c, 2)


def validate_coordinates(lat, lng):
    """Validate that coordinates are within reasonable bounds."""
    if lat is None or lng is None:
        return False
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 180


def format_location(address="", city="", state="", pincode=""):
    """Format location components into a readable string."""
    parts = [p for p in [address, city, state, pincode] if p]
    return ", ".join(parts) if parts else "Location not available"


def get_entity_location(db, table, user_id):
    """Get latitude/longitude for an entity from its profile table."""
    row = db.execute(
        text(f"SELECT latitude, longitude FROM {table} WHERE user_id=:uid"),
        {"uid": user_id},
    ).mappings().first()
    if row and row["latitude"] is not None and row["longitude"] is not None:
        return (row["latitude"], row["longitude"])
    return (None, None)


def get_location_for_user(db, user_id):
    """Get location for any user, checking their profile table based on role."""
    user = db.execute(
        text("SELECT * FROM users WHERE id=:uid"), {"uid": user_id}
    ).mappings().first()
    if not user:
        return None

    role = user["role"]
    result = {
        "lat": user.get("latitude"),
        "lng": user.get("longitude"),
        "address": user["address"] or "",
        "city": user.get("city") or "",
        "state": user.get("state") or "",
        "pincode": user.get("pincode") or "",
    }

    profile_tables = {
        "fpo": "fpo_profiles",
        "bulk_buyer": "bulk_buyer_profiles",
        "logistics": "logistics_profiles",
    }
    if role in profile_tables:
        profile = db.execute(
            text(f"SELECT * FROM {profile_tables[role]} WHERE user_id=:uid"),
            {"uid": user_id},
        ).mappings().first()
        if profile:
            if profile["latitude"] is not None:
                result["lat"] = profile["latitude"]
                result["lng"] = profile["longitude"]
            result["address"] = profile["address"] or result["address"]
            result["city"] = profile["city"] or result["city"]
            result["state"] = profile["state"] or result["state"]
            result["pincode"] = profile["pincode"] or result["pincode"]

    return result


def get_distance_between_users(db, user_id1, user_id2):
    """Calculate distance between two users using their stored coordinates."""
    loc1 = get_location_for_user(db, user_id1)
    loc2 = get_location_for_user(db, user_id2)
    if not loc1 or not loc2:
        return 0.0
    if loc1["lat"] is None or loc2["lat"] is None:
        return 0.0
    return haversine(loc1["lat"], loc1["lng"], loc2["lat"], loc2["lng"])


def get_distance_from_coords(lat1, lng1, lat2, lng2):
    """Calculate distance between two coordinate pairs."""
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return 0.0
    return haversine(lat1, lng1, lat2, lng2)


def find_nearby_entities(db, table, center_lat, center_lng, max_distance_km=50, limit=20):
    """Find entities within a certain distance from a center point."""
    if not validate_coordinates(center_lat, center_lng):
        return []

    rows = db.execute(
        text(f"SELECT * FROM {table} WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    ).mappings().fetchall()
    results = []
    for row in rows:
        dist = haversine(center_lat, center_lng, row["latitude"], row["longitude"])
        if dist <= max_distance_km:
            result = dict(row)
            result["distance_km"] = dist
            results.append(result)

    results.sort(key=lambda x: x["distance_km"])
    return results[:limit]
