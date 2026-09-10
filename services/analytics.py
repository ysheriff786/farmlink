
from sqlalchemy import text


def get_farmer_analytics(db, farmer_id):
    """Get comprehensive analytics for a farmer.

    Returns:
        dict with revenue, orders, products, buyers, realization metrics
    """
    stats = db.execute(
        text(
            "SELECT COUNT(DISTINCT o.id) as order_count, "
            "SUM(oi.quantity * oi.price) as revenue, "
            "SUM(oi.quantity) as quantity_sold, "
            "COUNT(DISTINCT o.customer_id) as unique_buyers "
            "FROM order_items oi "
            "JOIN orders o ON o.id = oi.order_id "
            "WHERE oi.farmer_id=:fid AND o.status != 'Cancelled'"
        ),
        {"fid": farmer_id},
    ).mappings().first()

    repeat_buyers = db.execute(
        text(
            "SELECT COUNT(*) as n FROM ("
            "SELECT customer_id, COUNT(*) as cnt "
            "FROM order_items oi "
            "JOIN orders o ON o.id = oi.order_id "
            "WHERE oi.farmer_id=:fid AND o.status != 'Cancelled' "
            "GROUP BY customer_id HAVING cnt > 1"
            ")"
        ),
        {"fid": farmer_id},
    ).mappings().first()["n"]

    top_products = db.execute(
        text(
            "SELECT oi.product_name, SUM(oi.quantity) as qty, "
            "SUM(oi.quantity * oi.price) as revenue "
            "FROM order_items oi "
            "JOIN orders o ON o.id = oi.order_id "
            "WHERE oi.farmer_id=:fid AND o.status != 'Cancelled' "
            "GROUP BY oi.product_name ORDER BY revenue DESC LIMIT 5"
        ),
        {"fid": farmer_id},
    ).mappings().fetchall()

    revenue = float(stats["revenue"] or 0)
    qty_sold = float(stats["quantity_sold"] or 0)
    from services.pricing import PLATFORM_FEE_PERCENT
    platform_fee = round(revenue * PLATFORM_FEE_PERCENT / 100, 2)
    net_realization = round(revenue - platform_fee, 2)
    avg_price = round(net_realization / qty_sold, 2) if qty_sold > 0 else 0

    return {
        "total_orders": stats["order_count"] or 0,
        "total_revenue": round(revenue, 2),
        "quantity_sold": qty_sold,
        "unique_buyers": stats["unique_buyers"] or 0,
        "repeat_buyers": repeat_buyers,
        "platform_fee": platform_fee,
        "net_realization": net_realization,
        "avg_price_per_unit": avg_price,
        "top_products": [dict(p) for p in top_products],
    }


def get_fpo_analytics(db, fpo_id):
    """Get comprehensive analytics for an FPO."""
    products = db.execute(
        text("SELECT COUNT(*) as n FROM products WHERE farmer_id=:fid"),
        {"fid": fpo_id},
    ).mappings().first()["n"]

    members = db.execute(
        text("SELECT COUNT(*) as n FROM fpo_members WHERE fpo_id=:fid AND status='approved'"),
        {"fid": fpo_id},
    ).mappings().first()["n"]

    aggregate_supplies = db.execute(
        text("SELECT COUNT(*) as n FROM products WHERE farmer_id=:fid AND is_aggregate=1"),
        {"fid": fpo_id},
    ).mappings().first()["n"]

    bulk_orders = db.execute(
        text(
            "SELECT COUNT(*) as n, COALESCE(SUM(total), 0) as revenue "
            "FROM bulk_orders WHERE fpo_id=:fid AND status='Active'"
        ),
        {"fid": fpo_id},
    ).mappings().first()

    quotes = db.execute(
        text("SELECT COUNT(*) as n FROM bulk_quotes WHERE fpo_id=:fid"),
        {"fid": fpo_id},
    ).mappings().first()["n"]

    accepted_quotes = db.execute(
        text("SELECT COUNT(*) as n FROM bulk_quotes WHERE fpo_id=:fid AND status='Accepted'"),
        {"fid": fpo_id},
    ).mappings().first()["n"]

    fulfillment_rate = round(accepted_quotes / quotes * 100, 1) if quotes > 0 else 0

    retail_revenue = db.execute(
        text(
            "SELECT COALESCE(SUM(oi.quantity * oi.price), 0) as revenue "
            "FROM order_items oi "
            "JOIN orders o ON o.id = oi.order_id "
            "WHERE oi.farmer_id=:fid AND o.status != 'Cancelled'"
        ),
        {"fid": fpo_id},
    ).mappings().first()["revenue"]

    total_revenue = round(float(retail_revenue or 0) + float(bulk_orders["revenue"] or 0), 2)

    member_supply = db.execute(
        text(
            "SELECT COALESCE(SUM(p.stock), 0) as total "
            "FROM products p "
            "JOIN fpo_members fm ON fm.farmer_id = p.farmer_id "
            "WHERE fm.fpo_id=:fid AND fm.status='approved'"
        ),
        {"fid": fpo_id},
    ).mappings().first()["total"]

    allocated = db.execute(
        text(
            "SELECT COALESCE(SUM(sa.committed_qty), 0) as total "
            "FROM supply_allocations sa "
            "JOIN products p ON p.id = sa.supply_id "
            "WHERE p.farmer_id=:fid"
        ),
        {"fid": fpo_id},
    ).mappings().first()["total"]

    utilization = round(float(allocated) / member_supply * 100, 1) if member_supply > 0 else 0

    return {
        "products": products,
        "members": members,
        "aggregate_supplies": aggregate_supplies,
        "bulk_orders": bulk_orders["n"] or 0,
        "bulk_revenue": round(float(bulk_orders["revenue"] or 0), 2),
        "total_quotes": quotes,
        "accepted_quotes": accepted_quotes,
        "fulfillment_rate": fulfillment_rate,
        "retail_revenue": round(float(retail_revenue or 0), 2),
        "total_revenue": total_revenue,
        "member_supply_kg": member_supply,
        "allocated_kg": allocated,
        "supply_utilization": utilization,
    }


def get_admin_analytics(db):
    """Get comprehensive analytics for the admin intelligence center."""
    users = db.execute(
        text("SELECT role, COUNT(*) as n FROM users GROUP BY role")
    ).mappings().fetchall()
    role_counts = {r["role"]: r["n"] for r in users}
    total_users = sum(role_counts.values())

    products = db.execute(text("SELECT COUNT(*) as n FROM products")).mappings().first()["n"]

    orders = db.execute(
        text(
            "SELECT COUNT(*) as n, COALESCE(SUM(total), 0) as revenue "
            "FROM orders WHERE status != 'Cancelled'"
        )
    ).mappings().first()

    active_orders = db.execute(
        text("SELECT COUNT(*) as n FROM orders WHERE status NOT IN ('Delivered','Cancelled')")
    ).mappings().first()["n"]

    pending_verifications = db.execute(
        text(
            "SELECT COUNT(*) as n FROM ("
            "SELECT user_id FROM fpo_profiles WHERE verification_status='pending' "
            "UNION ALL "
            "SELECT user_id FROM bulk_buyer_profiles WHERE verification_status='pending' "
            "UNION ALL "
            "SELECT user_id FROM logistics_profiles WHERE verification_status='pending'"
            ")"
        )
    ).mappings().first()["n"]

    supply = db.execute(
        text(
            "SELECT COALESCE(SUM(stock), 0) as total, "
            "COUNT(CASE WHEN is_aggregate=1 THEN 1 END) as aggregate_count "
            "FROM products"
        )
    ).mappings().first()

    bulk = db.execute(
        text(
            "SELECT COUNT(*) as requirements, "
            "SUM(CASE WHEN status='Open' THEN 1 ELSE 0 END) as open_reqs, "
            "SUM(CASE WHEN status='Awarded' THEN 1 ELSE 0 END) as awarded "
            "FROM bulk_requirements"
        )
    ).mappings().first()

    deliveries = db.execute(
        text(
            "SELECT COUNT(*) as n, "
            "SUM(CASE WHEN status='In Transit' THEN 1 ELSE 0 END) as in_transit, "
            "SUM(CASE WHEN status='Delivered' THEN 1 ELSE 0 END) as delivered "
            "FROM delivery_assignments"
        )
    ).mappings().first()

    logistics_partners = db.execute(
        text("SELECT COUNT(*) as n FROM logistics_profiles WHERE verification_status='verified'")
    ).mappings().first()["n"]

    farmer_revenue = db.execute(
        text(
            "SELECT oi.farmer_id, u.name, "
            "SUM(oi.quantity * oi.price) as revenue, "
            "SUM(oi.quantity) as qty, "
            "COUNT(DISTINCT o.customer_id) as buyers "
            "FROM order_items oi "
            "JOIN orders o ON o.id = oi.order_id "
            "JOIN users u ON u.id = oi.farmer_id "
            "WHERE o.status != 'Cancelled' "
            "GROUP BY oi.farmer_id ORDER BY revenue DESC"
        )
    ).mappings().fetchall()

    from services.pricing import PLATFORM_FEE_PERCENT
    total_revenue = float(orders["revenue"] or 0)
    platform_fees = round(total_revenue * PLATFORM_FEE_PERCENT / 100, 2)

    return {
        "total_users": total_users,
        "role_counts": role_counts,
        "products": products,
        "total_orders": orders["n"] or 0,
        "total_revenue": round(total_revenue, 2),
        "active_orders": active_orders,
        "pending_verifications": pending_verifications,
        "supply_kg": supply["total"] or 0,
        "aggregate_supplies": supply["aggregate_count"] or 0,
        "bulk_requirements": bulk["requirements"] or 0,
        "open_requirements": bulk["open_reqs"] or 0,
        "awarded_requirements": bulk["awarded"] or 0,
        "total_deliveries": deliveries["n"] or 0,
        "in_transit": deliveries["in_transit"] or 0,
        "delivered": deliveries["delivered"] or 0,
        "logistics_partners": logistics_partners,
        "platform_fees": platform_fees,
        "farmer_revenue_breakdown": [dict(r) for r in farmer_revenue],
    }


def get_bulk_buyer_analytics(db, buyer_id):
    """Get analytics for a bulk buyer."""
    requirements = db.execute(
        text("SELECT COUNT(*) as n FROM bulk_requirements WHERE buyer_id=:bid"),
        {"bid": buyer_id},
    ).mappings().first()["n"]

    orders = db.execute(
        text(
            "SELECT COUNT(*) as n, COALESCE(SUM(total), 0) as spending "
            "FROM bulk_orders WHERE buyer_id=:bid AND status='Active'"
        ),
        {"bid": buyer_id},
    ).mappings().first()

    completed = db.execute(
        text(
            "SELECT COUNT(*) as n FROM bulk_orders "
            "WHERE buyer_id=:bid AND status='Completed'"
        ),
        {"bid": buyer_id},
    ).mappings().first()["n"]

    return {
        "total_requirements": requirements,
        "active_orders": orders["n"] or 0,
        "total_spending": round(float(orders["spending"] or 0), 2),
        "completed_orders": completed,
    }
