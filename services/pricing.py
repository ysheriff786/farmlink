PLATFORM_FEE_PERCENT = 2.0
FPO_COMMISSION_PERCENT = 5.0


def calculate_price_breakdown(farmer_price, quantity, fpo_id=None,
                               logistics_cost=0, unit="kg"):
    """Calculate a transparent price breakdown for a transaction."""
    farmer_total = round(farmer_price * quantity, 2)
    platform_fee = round(farmer_total * PLATFORM_FEE_PERCENT / 100, 2)
    fpo_commission = round(farmer_total * FPO_COMMISSION_PERCENT / 100, 2) if fpo_id else 0

    buyer_price = round(farmer_total + fpo_commission + logistics_cost + platform_fee, 2)
    farmer_realization = round(farmer_total - platform_fee, 2)

    return {
        "farmer_price_per_unit": farmer_price,
        "quantity": quantity,
        "unit": unit,
        "farmer_total": farmer_total,
        "fpo_commission": fpo_commission,
        "logistics_cost": logistics_cost,
        "platform_fee": platform_fee,
        "buyer_price": buyer_price,
        "farmer_realization": farmer_realization,
        "fpo_id": fpo_id,
    }


def calculate_farmer_realization(order_total, quantity_sold, logistics_cost=0):
    """Calculate what the farmer actually received vs what the buyer paid."""
    platform_fee = round(order_total * PLATFORM_FEE_PERCENT / 100, 2)
    farmer_revenue = round(order_total - platform_fee - logistics_cost, 2)
    price_per_unit = round(farmer_revenue / quantity_sold, 2) if quantity_sold > 0 else 0

    return {
        "order_total": order_total,
        "platform_fee": platform_fee,
        "logistics_cost": logistics_cost,
        "farmer_revenue": farmer_revenue,
        "price_per_unit": price_per_unit,
        "quantity_sold": quantity_sold,
    }


def calculate_traditional_vs_direct(farmer_price, quantity, traditional_margin=30):
    """Compare direct sale vs traditional intermediary model."""
    direct_revenue = round(farmer_price * quantity, 2)
    traditional_price = round(farmer_price * (1 + traditional_margin / 100), 2)
    traditional_revenue = round(farmer_price * quantity * 0.6, 2)
    savings = round(direct_revenue - traditional_revenue, 2)

    return {
        "direct_price_per_unit": farmer_price,
        "direct_revenue": direct_revenue,
        "traditional_price_per_unit": traditional_price,
        "traditional_farmer_revenue": traditional_revenue,
        "farmer_savings": savings,
        "savings_percent": round(savings / traditional_revenue * 100, 1) if traditional_revenue > 0 else 0,
        "note": "Illustrative comparison based on assumed 30% traditional intermediary margin",
    }


def get_order_price_detail(db, order_id):
    """Get detailed price breakdown for an order."""
    from sqlalchemy import text
    order = db.execute(
        text("SELECT * FROM orders WHERE id=:oid"), {"oid": order_id}
    ).mappings().first()
    if not order:
        return None

    items = db.execute(
        text(
            "SELECT oi.*, p.category, p.unit, p.mrp, p.farmer_id "
            "FROM order_items oi "
            "LEFT JOIN products p ON p.name = oi.product_name AND p.farmer_id = oi.farmer_id "
            "WHERE oi.order_id=:oid"
        ),
        {"oid": order_id},
    ).mappings().fetchall()

    item_details = []
    for item in items:
        detail = {
            "product_name": item["product_name"],
            "quantity": item["quantity"],
            "price_per_unit": item["price"],
            "line_total": round(item["price"] * item["quantity"], 2),
            "mrp": item["mrp"] if item["mrp"] else None,
            "farmer_id": item["farmer_id"],
        }
        if detail["mrp"] and detail["mrp"] > detail["price_per_unit"]:
            detail["savings_per_unit"] = round(detail["mrp"] - detail["price_per_unit"], 2)
            detail["total_savings"] = round(detail["savings_per_unit"] * item["quantity"], 2)
        else:
            detail["savings_per_unit"] = 0
            detail["total_savings"] = 0
        item_details.append(detail)

    total_savings = sum(i["total_savings"] for i in item_details)
    platform_fee = round(order["total"] * PLATFORM_FEE_PERCENT / 100, 2)

    return {
        "order_id": order_id,
        "total": order["total"],
        "payment_method": order["payment_method"],
        "status": order["status"],
        "items": item_details,
        "total_savings": total_savings,
        "platform_fee": platform_fee,
        "farmer_revenue": round(order["total"] - platform_fee, 2),
    }
