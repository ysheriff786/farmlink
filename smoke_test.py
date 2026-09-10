import app as A

A.init_db()
c = A.app.test_client()

# --- public pages ---
for p in ["/", "/products", "/track", "/page/about", "/login", "/register", "/product/1"]:
    r = c.get(p)
    print(f"{r.status_code} GET {p}" + ("  <-- ERROR" if r.status_code >= 500 else ""))

# --- full flow: farmer adds product, customer buys ---
with c.session_transaction() as s:
    s["user_id"] = 1  # farmer
r = c.get("/product/new")
print(r.status_code, "GET /product/new")
r = c.post("/product/new", data={
    "name": "TestTomato", "description": "fresh", "price": "40",
    "mrp": "50", "category": "Vegetables", "unit": "kg", "stock": "10"})
print(r.status_code, "POST /product/new")

import sqlite3

conn = sqlite3.connect(A.DB_PATH)
conn.row_factory = sqlite3.Row
pid = conn.execute("SELECT id FROM products WHERE name='TestTomato'").fetchone()["id"]
conn.close()
print("created product", pid)

with c.session_transaction() as s:
    s["user_id"] = 3  # customer
r = c.post(f"/cart/add/{pid}", data={"quantity": "2"})
print(r.status_code, "POST cart/add")
r = c.get("/cart")
print(r.status_code, "GET /cart")
r = c.post("/place-order", data={"phone": "9876543210",
          "address": "123 Farm Street, Green City", "payment_method": "COD"})
print(r.status_code, "POST place-order")
r = c.get("/orders")
print(r.status_code, "GET /orders (customer)")

oid_row = sqlite3.connect(A.DB_PATH).execute(
    "SELECT MAX(id) FROM orders").fetchone()
oid = oid_row[0]
print("order id:", oid)

with c.session_transaction() as s:
    s["user_id"] = 1  # farmer
r = c.get("/orders")
print(r.status_code, "GET /orders (farmer)")
r = c.post(f"/order/{oid}/status", data={"status": "Confirmed"})
print(r.status_code, "POST status Confirmed")

with c.session_transaction() as s:
    s["user_id"] = 3
r = c.post(f"/product/{pid}/review", data={"rating": "5", "comment": "great"})
print(r.status_code, "POST review")
r = c.get(f"/product/{pid}")
print(r.status_code, f"GET /product/{pid} (detail with review)")

with c.session_transaction() as s:
    s["user_id"] = 1
c.post(f"/order/{oid}/status", data={"status": "Out for Delivery"})
with c.session_transaction() as s:
    s["user_id"] = 3
r = c.post(f"/order/{oid}/status", data={"status": "Cancelled"})
print(r.status_code, "customer tries cancel after OFD (should be blocked flash)")

# cleanup test product
conn = sqlite3.connect(A.DB_PATH)
conn.execute("DELETE FROM cart_items WHERE product_id=?", (pid,))
conn.execute("DELETE FROM reviews WHERE product_id=?", (pid,))
conn.execute("DELETE FROM products WHERE id=?", (pid,))
conn.commit()
conn.close()
print("cleanup done")
