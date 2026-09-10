from datetime import datetime

from sqlalchemy import text


def notify_user(db, user_id, title, message, ntype="info",
                reference_type="", reference_id=None):
    """Create a notification for a specific user.

    Args:
        user_id: Target user ID
        title: Notification title
        message: Notification body
        ntype: Type (info, order, delivery, quote, alert, system)
        reference_type: Related entity type (order, product, etc.)
        reference_id: Related entity ID
    """
    db.execute(
        text(
            "INSERT INTO notifications (user_id, type, title, message, "
            "reference_type, reference_id, is_read, created_at) "
            "VALUES (:uid, :ntype, :title, :msg, :rtype, :rid, 0, :ts)"
        ),
        {
            "uid": user_id, "ntype": ntype, "title": title, "msg": message,
            "rtype": reference_type, "rid": reference_id,
            "ts": datetime.utcnow().isoformat(),
        },
    )
    db.commit()


def notify_role(db, role, title, message, ntype="info",
                reference_type="", reference_id=None):
    """Create a notification for all users of a given role."""
    users = db.execute(
        text("SELECT id FROM users WHERE role=:role"), {"role": role}
    ).mappings().fetchall()
    for user in users:
        notify_user(db, user["id"], title, message, ntype,
                    reference_type, reference_id)


def get_notifications(db, user_id, limit=50, unread_only=False):
    """Get notifications for a user."""
    sql = "SELECT * FROM notifications WHERE user_id=:uid"
    params = {"uid": user_id}
    if unread_only:
        sql += " AND is_read=0"
    sql += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit
    return db.execute(text(sql), params).mappings().fetchall()


def get_unread_count(db, user_id):
    """Get count of unread notifications."""
    row = db.execute(
        text("SELECT COUNT(*) as n FROM notifications WHERE user_id=:uid AND is_read=0"),
        {"uid": user_id},
    ).mappings().first()
    return row["n"] if row else 0


def mark_as_read(db, notification_id, user_id):
    """Mark a single notification as read."""
    db.execute(
        text("UPDATE notifications SET is_read=1 WHERE id=:nid AND user_id=:uid"),
        {"nid": notification_id, "uid": user_id},
    )
    db.commit()


def mark_all_as_read(db, user_id):
    """Mark all notifications as read for a user."""
    db.execute(
        text("UPDATE notifications SET is_read=1 WHERE user_id=:uid AND is_read=0"),
        {"uid": user_id},
    )
    db.commit()


def delete_old_notifications(db, days=90):
    """Delete notifications older than specified days."""
    cutoff = datetime.utcnow().timestamp() - (days * 86400)
    cutoff_str = datetime.utcfromtimestamp(cutoff).isoformat()
    db.execute(
        text("DELETE FROM notifications WHERE created_at < :cutoff"),
        {"cutoff": cutoff_str},
    )
    db.commit()


def notify_order_status(db, order_id, new_status, buyer_id, farmer_ids):
    """Send notifications when order status changes."""
    status_messages = {
        "Confirmed": ("Order Confirmed", "Your order #{oid} has been confirmed by the farmer."),
        "Out for Delivery": ("Order Out for Delivery", "Your order #{oid} is out for delivery."),
        "Delivered": ("Order Delivered", "Your order #{oid} has been delivered successfully!"),
        "Cancelled": ("Order Cancelled", "Your order #{oid} has been cancelled."),
    }
    if new_status in status_messages:
        title, msg = status_messages[new_status]
        notify_user(db, buyer_id, title, msg.format(oid=order_id),
                    "order", "order", order_id)
        for fid in farmer_ids:
            notify_user(db, fid, f"Order #{order_id} {new_status}",
                        f"Order #{order_id} is now {new_status}.",
                        "order", "order", order_id)


def notify_quote_event(db, quote_id, event, fpo_id, buyer_id, requirement_title=""):
    """Send notifications for quote events."""
    events = {
        "submitted": ("New Quote Received",
                      f"A quote has been submitted for '{requirement_title}'."),
        "accepted": ("Quote Accepted",
                     f"Your quote for '{requirement_title}' has been accepted!"),
        "rejected": ("Quote Rejected",
                     f"Your quote for '{requirement_title}' was not selected."),
    }
    if event in events:
        title, msg = events[event]
        if event == "submitted":
            notify_user(db, buyer_id, title, msg, "quote", "quote", quote_id)
        else:
            notify_user(db, fpo_id, title, msg, "quote", "quote", quote_id)


def notify_delivery_event(db, assignment_id, event, logistics_id, order_id=None):
    """Send notifications for delivery events."""
    events = {
        "assigned": ("Delivery Assigned",
                     f"You have a new delivery assignment #{assignment_id}."),
        "picked_up": ("Pickup Complete",
                      f"Delivery #{assignment_id} has been picked up."),
        "in_transit": ("In Transit",
                       f"Delivery #{assignment_id} is now in transit."),
        "delivered": ("Delivery Completed",
                      f"Delivery #{assignment_id} has been completed."),
        "failed": ("Delivery Failed",
                   f"Delivery #{assignment_id} has failed."),
    }
    if event in events:
        title, msg = events[event]
        notify_user(db, logistics_id, title, msg, "delivery",
                    "delivery", assignment_id)


def notify_farmer_new_order(db, farmer_id, order_id, total):
    """Notify farmer of a new order."""
    notify_user(db, farmer_id, "New Order Received",
                f"You have a new order #{order_id} worth \u20b9{total:.2f}.",
                "order", "order", order_id)


def notify_supply_shortage(db, fpo_id, product_name, available, required):
    """Notify FPO of potential supply shortage."""
    notify_user(db, fpo_id, "Supply Shortage Alert",
                f"Low supply for {product_name}: {available:.1f} available vs {required:.1f} required.",
                "alert", "product", None)


def notify_admin_verification(db, admin_ids, entity_type, entity_name):
    """Notify admins of pending verification."""
    for admin_id in admin_ids:
        notify_user(db, admin_id, "Verification Required",
                    f"A new {entity_type} '{entity_name}' requires verification.",
                    "system", entity_type, None)
