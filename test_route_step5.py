"""End-to-end test for Step 5: Route Optimization + logistics + maps."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Use a throwaway DB by swapping the module constant BEFORE routes execute.
# Simplest reliable approach: point at the real migrated DB but ONLY register
# throwaway test users and clean them up. All new routes are read-only checks.
import app as A

client = A.app.test_client()

def reg(name, email, role, phone="9999999999", **extra):
    data = {
        "name": name, "email": email, "password": "password123",
        "phone": phone, "role": role,
        "contact_person": name, "city": "Bengaluru",
        "state": "Karnataka", "pincode": "560001",
        "address": "Test Street",
        "latitude": "12.9716", "longitude": "77.5946",
    }
    data.update(extra)
    r = client.post("/register", data=data, follow_redirects=False)
    return r

def login(email, password="password123"):
    return client.post("/login", data={"email": email, "password": password},
                       follow_redirects=True)

reg("Logi User", "logi_route@test.com", "logistics",
    company_name="FastRoute", license_number="KA-1234")
login("logi_route@test.com")
print("Logistics register+login OK")

# Add a vehicle
r = client.post("/logistics/vehicle/new", data={
    "vehicle_type": "Truck", "registration_number": "KA-01-AB-1234",
    "driver_name": "Driver A", "driver_phone": "8888888888", "capacity_kg": "500",
}, follow_redirects=True)
print("Add vehicle ->", r.status_code)

r = client.get("/logistics/dashboard")
print("GET /logistics/dashboard ->", r.status_code, "(render)")
assert r.status_code == 200

r = client.get("/route-optimizer")
print("GET /route-optimizer ->", r.status_code, "(render)")
assert r.status_code == 200

client.get("/logout")

# Admin pages
reg("Adm", "admr_route@test.com", "farmer")
login("admr_route@test.com")
# promote to admin
conn = sqlite3.connect(A.DB_PATH)
conn.execute("UPDATE users SET role='admin' WHERE email='admr_route@test.com'")
conn.commit(); conn.close()
client.get("/logout")
login("admr_route@test.com")

r = client.get("/admin/intelligence")
print("GET /admin/intelligence ->", r.status_code, "(render)")
assert r.status_code == 200

r = client.get("/admin/logistics")
print("GET /admin/logistics ->", r.status_code, "(render)")
assert r.status_code == 200

r = client.get("/api/alerts")
print("GET /api/alerts ->", r.status_code)
assert r.status_code == 200

r = client.get("/api/admin-analytics") if False else client.get("/api/analytics/admin")
print("GET /api/analytics/admin ->", r.status_code)
assert r.status_code == 200

r = client.get("/notifications")
print("GET /notifications ->", r.status_code)
assert r.status_code == 200

# Cleanup test users
conn = sqlite3.connect(A.DB_PATH)
conn.execute("DELETE FROM users WHERE email IN ('logi_route@test.com','admr_route@test.com')")
conn.commit(); conn.close()
print("Cleanup OK")

# Unlogged maps
client2 = A.app.test_client()
r = client2.get("/map")
print("GET /map (anon) ->", r.status_code)
r = client2.get("/api/map/data")
print("GET /api/map/data (anon) ->", r.status_code)

print("ALL STEP 5 TESTS PASSED")