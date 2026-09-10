from datetime import datetime

from sqlalchemy import text

DELIVERY_STATUSES = [
    "Assigned", "Pickup Pending", "Picked Up", "In Transit",
    "Arrived", "Delivered", "Failed", "Cancelled"
]


def create_delivery_assignment(db, order_id=None, bulk_order_id=None,
                                logistics_id=None, vehicle_id=None,
                                pickup_address="", pickup_city="",
                                pickup_latitude=None, pickup_longitude=None,
                                destination_address="", destination_city="",
                                destination_latitude=None, destination_longitude=None,
                                required_capacity_kg=0,
                                notes=""):
    """Create a new delivery assignment."""
    if not logistics_id or not vehicle_id:
        return None
    if not order_id and not bulk_order_id:
        return None

    vehicle = db.execute(
        text("SELECT * FROM vehicles WHERE id=:vid AND logistics_id=:lid AND is_available=1"),
        {"vid": vehicle_id, "lid": logistics_id},
    ).mappings().first()
    if not vehicle:
        return None

    cur = db.execute(
        text(
            "INSERT INTO delivery_assignments "
            "(order_id, bulk_order_id, logistics_id, vehicle_id, "
            "pickup_address, pickup_city, pickup_latitude, pickup_longitude, "
            "destination_address, destination_city, destination_latitude, destination_longitude, "
            "required_capacity_kg, status, notes, assigned_at) "
            "VALUES (:oid, :boid, :lid, :vid, "
            ":pa, :pc, :plat, :plng, "
            ":da, :dc, :dlat, :dlng, "
            ":cap, 'Assigned', :notes, :ts)"
        ),
        {
            "oid": order_id, "boid": bulk_order_id, "lid": logistics_id,
            "vid": vehicle_id, "pa": pickup_address, "pc": pickup_city,
            "plat": pickup_latitude, "plng": pickup_longitude,
            "da": destination_address, "dc": destination_city,
            "dlat": destination_latitude, "dlng": destination_longitude,
            "cap": required_capacity_kg or 0, "notes": notes,
            "ts": datetime.utcnow().isoformat(),
        },
    )
    db.commit()
    return get_assignment(db, cur.lastrowid)


def get_assignment(db, assignment_id):
    """Get a delivery assignment by ID."""
    return db.execute(
        text(
            "SELECT da.*, v.vehicle_type, v.registration_number, v.driver_name, v.driver_phone, "
            "v.capacity_kg, u.name as logistics_company "
            "FROM delivery_assignments da "
            "LEFT JOIN vehicles v ON v.id = da.vehicle_id "
            "LEFT JOIN users u ON u.id = da.logistics_id "
            "WHERE da.id=:aid"
        ),
        {"aid": assignment_id},
    ).mappings().first()


def get_assignments_for_logistics(db, logistics_id, status=None):
    """Get all assignments for a logistics partner."""
    sql = (
        "SELECT da.*, v.vehicle_type, v.registration_number, v.driver_name, "
        "v.driver_phone, v.capacity_kg "
        "FROM delivery_assignments da "
        "LEFT JOIN vehicles v ON v.id = da.vehicle_id "
        "WHERE da.logistics_id=:lid"
    )
    params = {"lid": logistics_id}
    if status:
        sql += " AND da.status=:status"
        params["status"] = status
    sql += " ORDER BY da.created_at DESC"
    return db.execute(text(sql), params).mappings().fetchall()


def get_assignments_for_order(db, order_id):
    """Get all delivery assignments for an order."""
    return db.execute(
        text(
            "SELECT da.*, v.vehicle_type, v.registration_number, v.driver_name, "
            "u.name as logistics_company "
            "FROM delivery_assignments da "
            "LEFT JOIN vehicles v ON v.id = da.vehicle_id "
            "LEFT JOIN users u ON u.id = da.logistics_id "
            "WHERE da.order_id=:oid ORDER BY da.created_at DESC"
        ),
        {"oid": order_id},
    ).mappings().fetchall()


def update_assignment_status(db, assignment_id, new_status, user_id=None):
    """Update delivery assignment status with validation."""
    valid_transitions = {
        "Assigned": ["Pickup Pending", "Cancelled"],
        "Pickup Pending": ["Picked Up", "Failed", "Cancelled"],
        "Picked Up": ["In Transit", "Failed"],
        "In Transit": ["Arrived", "Failed"],
        "Arrived": ["Delivered", "Failed"],
    }

    assignment = get_assignment(db, assignment_id)
    if not assignment:
        return False, "Assignment not found"

    current = assignment["status"]
    if current in ("Delivered", "Failed", "Cancelled"):
        return False, f"Cannot change status from {current}"

    allowed = valid_transitions.get(current, [])
    if new_status not in allowed:
        return False, f"Cannot transition from {current} to {new_status}"

    if user_id and assignment["logistics_id"] != user_id:
        admin = db.execute(
            text("SELECT role FROM users WHERE id=:uid"), {"uid": user_id}
        ).mappings().first()
        if not admin or admin["role"] != "admin":
            return False, "Not authorized"

    now = datetime.utcnow().isoformat()
    updates = {"status": new_status}
    if new_status == "Picked Up":
        updates["picked_up_at"] = now
    elif new_status in ("Delivered", "Failed", "Cancelled"):
        updates["delivered_at"] = now

    set_parts = []
    params = {"aid": assignment_id}
    for k, v in updates.items():
        set_parts.append(f"{k}=:{k}")
        params[k] = v
    db.execute(
        text(f"UPDATE delivery_assignments SET {', '.join(set_parts)} WHERE id=:aid"),
        params,
    )

    if new_status in ("Delivered", "Failed", "Cancelled"):
        db.execute(
            text("UPDATE vehicles SET is_available=1 WHERE id=:vid"),
            {"vid": assignment["vehicle_id"]},
        )

    db.commit()
    return True, "Updated"


def get_available_vehicles(db, logistics_id):
    """Get available vehicles for a logistics partner."""
    return db.execute(
        text("SELECT * FROM vehicles WHERE logistics_id=:lid AND is_available=1"),
        {"lid": logistics_id},
    ).mappings().fetchall()


def get_all_vehicles(db, logistics_id):
    """Get all vehicles for a logistics partner."""
    return db.execute(
        text("SELECT * FROM vehicles WHERE logistics_id=:lid"),
        {"lid": logistics_id},
    ).mappings().fetchall()


def create_vehicle(db, logistics_id, vehicle_type="Truck", registration_number="",
                   driver_name="", driver_phone="", capacity_kg=0, capacity_volume=0):
    """Create a new vehicle."""
    cur = db.execute(
        text(
            "INSERT INTO vehicles (logistics_id, vehicle_type, registration_number, "
            "driver_name, driver_phone, capacity_kg, capacity_volume) "
            "VALUES (:lid, :vtype, :reg, :dname, :dphone, :cap, :cvol)"
        ),
        {
            "lid": logistics_id, "vtype": vehicle_type, "reg": registration_number,
            "dname": driver_name, "dphone": driver_phone,
            "cap": capacity_kg, "cvol": capacity_volume,
        },
    )
    db.commit()
    return db.execute(
        text("SELECT * FROM vehicles WHERE id=:vid"), {"vid": cur.lastrowid}
    ).mappings().first()


def update_vehicle(db, vehicle_id, logistics_id, **kwargs):
    """Update a vehicle. Only the owning logistics partner can update."""
    vehicle = db.execute(
        text("SELECT * FROM vehicles WHERE id=:vid AND logistics_id=:lid"),
        {"vid": vehicle_id, "lid": logistics_id},
    ).mappings().first()
    if not vehicle:
        return False

    allowed_fields = {"vehicle_type", "registration_number", "driver_name",
                      "driver_phone", "capacity_kg", "capacity_volume", "is_available"}
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not updates:
        return True

    set_parts = []
    params = {"vid": vehicle_id}
    for k, v in updates.items():
        set_parts.append(f"{k}=:{k}")
        params[k] = v
    db.execute(
        text(f"UPDATE vehicles SET {', '.join(set_parts)} WHERE id=:vid"),
        params,
    )
    db.commit()
    return True


def delete_vehicle(db, vehicle_id, logistics_id):
    """Delete a vehicle. Only if no active assignments."""
    vehicle = db.execute(
        text("SELECT * FROM vehicles WHERE id=:vid AND logistics_id=:lid"),
        {"vid": vehicle_id, "lid": logistics_id},
    ).mappings().first()
    if not vehicle:
        return False, "Vehicle not found"

    active = db.execute(
        text(
            "SELECT COUNT(*) as n FROM delivery_assignments "
            "WHERE vehicle_id=:vid AND status NOT IN ('Delivered','Failed','Cancelled')"
        ),
        {"vid": vehicle_id},
    ).mappings().first()["n"]
    if active > 0:
        return False, "Vehicle has active assignments"

    db.execute(text("DELETE FROM vehicles WHERE id=:vid"), {"vid": vehicle_id})
    db.commit()
    return True, "Deleted"


def get_logistics_stats(db, logistics_id):
    """Get stats for a logistics partner dashboard."""
    total = db.execute(
        text("SELECT COUNT(*) as n FROM delivery_assignments WHERE logistics_id=:lid"),
        {"lid": logistics_id},
    ).mappings().first()["n"]
    active = db.execute(
        text(
            "SELECT COUNT(*) as n FROM delivery_assignments "
            "WHERE logistics_id=:lid AND status NOT IN ('Delivered','Failed','Cancelled')"
        ),
        {"lid": logistics_id},
    ).mappings().first()["n"]
    delivered = db.execute(
        text(
            "SELECT COUNT(*) as n FROM delivery_assignments "
            "WHERE logistics_id=:lid AND status='Delivered'"
        ),
        {"lid": logistics_id},
    ).mappings().first()["n"]
    vehicles = db.execute(
        text("SELECT COUNT(*) as n FROM vehicles WHERE logistics_id=:lid"),
        {"lid": logistics_id},
    ).mappings().first()["n"]
    available = db.execute(
        text("SELECT COUNT(*) as n FROM vehicles WHERE logistics_id=:lid AND is_available=1"),
        {"lid": logistics_id},
    ).mappings().first()["n"]
    return {
        "total_assignments": total,
        "active": active,
        "delivered": delivered,
        "vehicles": vehicles,
        "available_vehicles": available,
    }


def get_all_active_deliveries(db):
    """Get all active deliveries for admin view."""
    return db.execute(
        text(
            "SELECT da.*, v.vehicle_type, v.registration_number, v.driver_name, "
            "u.name as logistics_company "
            "FROM delivery_assignments da "
            "LEFT JOIN vehicles v ON v.id = da.vehicle_id "
            "LEFT JOIN users u ON u.id = da.logistics_id "
            "WHERE da.status NOT IN ('Delivered','Failed','Cancelled') "
            "ORDER BY da.created_at DESC"
        )
    ).mappings().fetchall()


def auto_assign_delivery(db, order_id=None, bulk_order_id=None,
                         pickup_address="", pickup_city="",
                         pickup_latitude=None, pickup_longitude=None,
                         destination_address="", destination_city="",
                         destination_latitude=None, destination_longitude=None,
                         required_capacity_kg=0):
    """Auto-assign a delivery to the best available logistics partner."""
    partners = db.execute(
        text(
            "SELECT lp.user_id, u.name, lp.latitude, lp.longitude, lp.city "
            "FROM logistics_profiles lp "
            "JOIN users u ON u.id = lp.user_id "
            "WHERE lp.verification_status='verified'"
        )
    ).mappings().fetchall()

    if not partners:
        return None, "No verified logistics partners available"

    best_partner = None
    best_vehicle = None
    best_distance = float("inf")

    for partner in partners:
        vehicles = db.execute(
            text(
                "SELECT * FROM vehicles "
                "WHERE logistics_id=:lid AND is_available=1 AND capacity_kg>=:cap "
                "ORDER BY capacity_kg ASC"
            ),
            {"lid": partner["user_id"], "cap": required_capacity_kg},
        ).mappings().fetchall()

        for vehicle in vehicles:
            dist = 0.0
            if pickup_latitude and pickup_longitude and partner["latitude"] and partner["longitude"]:
                from services.location import haversine
                dist = haversine(pickup_latitude, pickup_longitude,
                                 partner["latitude"], partner["longitude"])
            elif pickup_city and partner["city"]:
                dist = 0.0

            if dist < best_distance:
                best_distance = dist
                best_partner = partner
                best_vehicle = vehicle

    if not best_partner:
        return None, "No vehicle with sufficient capacity available"

    assignment = create_delivery_assignment(
        db,
        order_id=order_id,
        bulk_order_id=bulk_order_id,
        logistics_id=best_partner["user_id"],
        vehicle_id=best_vehicle["id"],
        pickup_address=pickup_address,
        pickup_city=pickup_city,
        pickup_latitude=pickup_latitude,
        pickup_longitude=pickup_longitude,
        destination_address=destination_address,
        destination_city=destination_city,
        destination_latitude=destination_latitude,
        destination_longitude=destination_longitude,
        required_capacity_kg=required_capacity_kg or 0,
    )

    if assignment:
        db.execute(
            text("UPDATE vehicles SET is_available=0 WHERE id=:vid"),
            {"vid": best_vehicle["id"]},
        )
        db.commit()
        return assignment, None

    return None, "Failed to create assignment"
