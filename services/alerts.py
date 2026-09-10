from datetime import datetime

from sqlalchemy import text


def check_supply_shortage(db):
    """Check for products where demand may exceed supply."""
    alerts = []
    products = db.execute(
        text(
            "SELECT p.id, p.name, p.stock, p.farmer_id, p.category, "
            "u.name as farmer_name "
            "FROM products p "
            "JOIN users u ON u.id = p.farmer_id "
            "WHERE p.stock > 0"
        )
    ).mappings().fetchall()

    for product in products:
        recent_demand = db.execute(
            text(
                "SELECT COALESCE(SUM(oi.quantity), 0) as total "
                "FROM order_items oi "
                "JOIN orders o ON o.id = oi.order_id "
                "WHERE oi.product_name=:pname AND o.status != 'Cancelled' "
                "AND o.created_at >= datetime('now', '-7 days')"
            ),
            {"pname": product["name"]},
        ).mappings().first()["total"]

        if recent_demand > 0:
            days_of_stock = product["stock"] / (recent_demand / 7) if recent_demand > 0 else 999
            if days_of_stock < 3:
                alerts.append({
                    "type": "supply_shortage",
                    "severity": "high" if days_of_stock < 1 else "medium",
                    "title": f"Low Stock: {product['name']}",
                    "message": f"Only {product['stock']} units left ({days_of_stock:.1f} days of stock at current demand rate)",
                    "product_id": product["id"],
                    "farmer_id": product["farmer_id"],
                    "farmer_name": product["farmer_name"],
                    "stock": product["stock"],
                    "daily_demand": round(recent_demand / 7, 1),
                })

    return alerts


def check_demand_increase(db):
    """Check for products with significantly increasing demand."""
    alerts = []
    products = db.execute(
        text("SELECT DISTINCT product_name FROM order_items")
    ).mappings().fetchall()

    for row in products:
        product_name = row["product_name"]

        recent = db.execute(
            text(
                "SELECT COALESCE(SUM(quantity), 0) as total "
                "FROM order_items oi "
                "JOIN orders o ON o.id = oi.order_id "
                "WHERE oi.product_name=:pname AND o.status != 'Cancelled' "
                "AND o.created_at >= datetime('now', '-7 days')"
            ),
            {"pname": product_name},
        ).mappings().first()["total"]

        previous = db.execute(
            text(
                "SELECT COALESCE(SUM(quantity), 0) as total "
                "FROM order_items oi "
                "JOIN orders o ON o.id = oi.order_id "
                "WHERE oi.product_name=:pname AND o.status != 'Cancelled' "
                "AND o.created_at >= datetime('now', '-14 days') "
                "AND o.created_at < datetime('now', '-7 days')"
            ),
            {"pname": product_name},
        ).mappings().first()["total"]

        if previous > 0 and recent > previous * 1.5:
            increase_pct = round((recent - previous) / previous * 100, 1)
            alerts.append({
                "type": "demand_increase",
                "severity": "medium",
                "title": f"Demand Rising: {product_name}",
                "message": f"Demand increased {increase_pct}% in the last 7 days",
                "product_name": product_name,
                "recent_demand": recent,
                "previous_demand": previous,
                "increase_percent": increase_pct,
            })

    return alerts


def check_logistics_delays(db):
    """Check for deliveries that may be delayed."""
    alerts = []
    active = db.execute(
        text(
            "SELECT da.*, u.name as logistics_company "
            "FROM delivery_assignments da "
            "JOIN users u ON u.id = da.logistics_id "
            "WHERE da.status IN ('Picked Up', 'In Transit') "
            "AND da.picked_up_at IS NOT NULL"
        )
    ).mappings().fetchall()

    for assignment in active:
        picked_up = datetime.fromisoformat(assignment["picked_up_at"])
        hours_elapsed = (datetime.utcnow() - picked_up).total_seconds() / 3600

        if hours_elapsed > 24:
            alerts.append({
                "type": "logistics_delay",
                "severity": "high" if hours_elapsed > 48 else "medium",
                "title": f"Delivery #{assignment['id']} May Be Delayed",
                "message": f"Assignment has been {assignment['status'].lower()} for {hours_elapsed:.0f} hours",
                "assignment_id": assignment["id"],
                "logistics_company": assignment["logistics_company"],
                "hours_elapsed": round(hours_elapsed, 1),
            })

    return alerts


def check_capacity_issues(db):
    """Check for orders where quantity exceeds typical vehicle capacity."""
    alerts = []
    large_orders = db.execute(
        text(
            "SELECT bo.id, bo.title, bo.quantity, bo.buyer_id, "
            "u.name as buyer_name "
            "FROM bulk_orders bo "
            "JOIN users u ON u.id = bo.buyer_id "
            "WHERE bo.status='Active'"
        )
    ).mappings().fetchall()

    for order in large_orders:
        if order["quantity"] > 2000:
            alerts.append({
                "type": "capacity_issue",
                "severity": "medium",
                "title": f"Bulk Order #{order['id']} Needs Large Vehicle",
                "message": f"Order '{order['title']}' requires {order['quantity']:.0f} kg capacity",
                "order_id": order["id"],
                "quantity": order["quantity"],
                "buyer_name": order["buyer_name"],
            })

    return alerts


def get_all_alerts(db):
    """Get all active alerts from all checks."""
    alerts = []
    alerts.extend(check_supply_shortage(db))
    alerts.extend(check_demand_increase(db))
    alerts.extend(check_logistics_delays(db))
    alerts.extend(check_capacity_issues(db))

    severity_order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: severity_order.get(a.get("severity", "low"), 2))

    return alerts


def get_alerts_for_farmer(db, farmer_id):
    """Get alerts relevant to a specific farmer."""
    all_alerts = get_all_alerts(db)
    return [a for a in all_alerts if a.get("farmer_id") == farmer_id]


def get_alerts_for_fpo(db, fpo_id):
    """Get alerts relevant to a specific FPO."""
    all_alerts = get_all_alerts(db)
    member_ids = db.execute(
        text("SELECT farmer_id FROM fpo_members WHERE fpo_id=:fid AND status='approved'"),
        {"fid": fpo_id},
    ).mappings().fetchall()
    member_id_set = {m["farmer_id"] for m in member_ids}
    member_id_set.add(fpo_id)

    return [a for a in all_alerts
            if a.get("farmer_id") in member_id_set or
            a.get("logistics_company") is not None or
            a.get("type") in ("demand_increase", "capacity_issue")]
