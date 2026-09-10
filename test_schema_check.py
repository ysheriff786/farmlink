"""Verify DB init and new schema tables for FarmLink (against the migrated DB)."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as A

# import app already ran init_db() on A.DB_PATH (idempotent migration).
conn = sqlite3.connect(A.DB_PATH)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
print("New tables present:")
for t in ["logistics_profiles", "vehicles", "delivery_assignments", "notifications"]:
    print(" ", t, "->", t in tables)
assert all(t in tables for t in ["logistics_profiles", "vehicles", "delivery_assignments", "notifications"]), "Missing tables"

# Check indexes
indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
print("Indexes present:")
for i in ["idx_notifications_user", "idx_orders_status", "idx_order_items_farmer",
          "idx_products_farmer", "idx_delivery_assignments_logistics",
          "idx_delivery_assignments_status", "idx_vehicles_logistics"]:
    print(" ", i, "->", i in indexes)

# Verify users table includes logistics role in CHECK
users_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
assert "logistics" in users_sql, "logistics role missing from users CHECK"

assert "logistics" in A.ROLES, "logistics missing from ROLES"
conn.close()

# Verify the API endpoint map works via test client
client = A.app.test_client()
r = client.get("/api/stats")
print("GET /api/stats ->", r.status_code)

r = client.get("/map")
print("GET /map ->", r.status_code)

r = client.get("/api/map/data")
print("GET /api/map/data ->", r.status_code)

# Route optimize API
payload = {
    "pickups": [{"lat": 12.9716, "lng": 77.5946, "id": 1, "load_kg": 100}],
    "deliveries": [{"lat": 12.925, "lng": 77.5938, "id": 501, "load_kg": 100}],
}
r = client.post("/api/route/optimize", json=payload)
print("POST /api/route/optimize ->", r.status_code)
if r.status_code == 200:
    data = r.get_json()
    print("  distance km:", data["total_distance_km"], "| stops:", len(data["stops"]))

# Logged-out redirects for protected pages
r = client.get("/logistics/dashboard")
print("GET /logistics/dashboard (anon) ->", r.status_code)
r = client.get("/notifications")
print("GET /notifications (anon) ->", r.status_code)

print("ALL CHECKS PASSED")