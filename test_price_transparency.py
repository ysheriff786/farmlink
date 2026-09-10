import os
import sys

sys.path.insert(0, os.getcwd())
import sqlite3

import app as A

client = A.app.test_client()

def register(name, email, role, pwd="password123"):
    return client.post("/register", data={
        "name": name, "email": email, "password": pwd, "phone": "9999999999",
        "role": role, "contact_person": name, "city": "Bengaluru",
        "state": "Karnataka", "pincode": "560001",
        "latitude": "12.9716", "longitude": "77.5946",
    }, follow_redirects=False)

register("Farmer", "far_px@test.com", "farmer")
client.post("/login", data={"email": "far_px@test.com", "password": "password123"},
            follow_redirects=True)

# Create a product for the farmer
r = client.post("/product/new", data={
    "name": "Pumpkin", "category": "Vegetables", "price": "30", "unit": "kg",
    "stock": "100", "description": "Farm fresh", "mrp": "45",
}, follow_redirects=True)

# Get the product id and build a direct order against it (simulate order flow)
conn = sqlite3.connect(A.DB_PATH)
row = conn.execute("SELECT id, farmer_id FROM products WHERE name='Pumpkin' AND is_aggregate=0 LIMIT 1").fetchone()
pid, farmer_id = row
conn.execute("INSERT INTO orders (customer_id, total, status, payment_method, address, phone) VALUES (?,?,?,?,?,?)",
             (farmer_id, 1200, "Delivered", "COD", "addr", "9999999999"))
oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
conn.execute("INSERT INTO order_items (order_id, product_name, farmer_id, quantity, price) VALUES (?,?,?,?,?)",
             (oid, "Pumpkin", farmer_id, 40, 30))
conn.commit(); conn.close()

r = client.get("/dashboard")
print("GET /dashboard (farmer) ->", r.status_code)
assert r.status_code == 200, r.status_code
html = r.data.decode("utf-8")
assert "Price transparency" in html, "Price transparency panel missing"
conn = sqlite3.connect(A.DB_PATH)
conn.execute("DELETE FROM order_items WHERE product_name='Pumpkin'")
conn.execute("DELETE FROM orders WHERE customer_id=?", (farmer_id,))
conn.execute("DELETE FROM products WHERE name='Pumpkin'")
conn.execute("DELETE FROM users WHERE email='far_px@test.com'")
conn.commit(); conn.close()
print("FARMER PRICE TRANSPARENCY OK")