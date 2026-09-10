import sqlite3

import app as A

A.init_db()
c = A.app.test_client()
ok = True


def check(label, cond):
    global ok
    print(("OK  " if cond else "FAIL") + " " + label)
    ok = ok and cond


# 1. migration: delivery_otp column exists
conn = sqlite3.connect(A.DB_PATH)
cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
check("delivery_otp column migrated", "delivery_otp" in cols)

# fresh product
with c.session_transaction() as s:
    s["user_id"] = 1  # farmer
c.post("/product/new", data={"name": "OTPTomato", "description": "x", "price": "10",
        "category": "Vegetables", "unit": "kg", "stock": "5"})
pid = sqlite3.connect(A.DB_PATH).execute(
    "SELECT id FROM products WHERE name='OTPTomato'").fetchone()[0]

# customer orders it (COD) - fresh account so old orders don't interfere
import random

c.post("/register", data={
    "name": "Test Buyer", "email": f"buyer{random.randint(10000,99999)}@test.com",
    "password": "secret1", "role": "customer", "phone": "9876500000"})
with c.session_transaction() as s:
    buyer = s["user_id"]
c.post(f"/cart/add/{pid}", data={"quantity": "1"})
c.post("/place-order", data={"phone": "9876543210", "address": "1 Test Lane Metro City",
        "payment_method": "COD"})
oid = sqlite3.connect(A.DB_PATH).execute("SELECT MAX(id) FROM orders").fetchone()[0]
print("test order:", oid)

# customer sees NO otp yet
page = c.get("/orders").get_data(as_text=True)
check("no OTP before dispatch", 'class="otp-code"' not in page)

# farmer: Placed -> Confirmed -> Out for Delivery (auto-generates OTP)
with c.session_transaction() as s:
    s["user_id"] = 1
r = c.post(f"/order/{oid}/status", data={"status": "Confirmed"}, follow_redirects=True)
r = c.post(f"/order/{oid}/status", data={"status": "Out for Delivery"}, follow_redirects=True)
otp = sqlite3.connect(A.DB_PATH).execute(
    "SELECT delivery_otp FROM orders WHERE id=?", (oid,)).fetchone()[0]
check("OTP auto-generated on Out for Delivery", bool(otp and len(otp) == 6))
print("   generated OTP:", otp)

# wrong OTP must NOT deliver
r = c.post(f"/order/{oid}/status", data={"status": "Delivered", "otp": "000000"},
           follow_redirects=True)
st = sqlite3.connect(A.DB_PATH).execute(
    "SELECT status FROM orders WHERE id=?", (oid,)).fetchone()[0]
check("wrong OTP rejected, status unchanged", st == "Out for Delivery" and b"Wrong" in r.data or st == "Out for Delivery")

# empty OTP must NOT deliver
r = c.post(f"/order/{oid}/status", data={"status": "Delivered"}, follow_redirects=True)
st = sqlite3.connect(A.DB_PATH).execute(
    "SELECT status FROM orders WHERE id=?", (oid,)).fetchone()[0]
check("missing OTP rejected", st == "Out for Delivery")

# correct OTP delivers
r = c.post(f"/order/{oid}/status", data={"status": "Delivered", "otp": otp},
           follow_redirects=True)
st = sqlite3.connect(A.DB_PATH).execute(
    "SELECT status FROM orders WHERE id=?", (oid,)).fetchone()[0]
check("correct OTP marks Delivered", st == "Delivered")

# customer page shows OTP box only while OFD (now delivered so not shown)
with c.session_transaction() as s:
    s["user_id"] = buyer
page = c.get("/orders").get_data(as_text=True)
check("customer had OTP UI available (chat present)", "Chat with farmer" in page)

# 2. CHAT: customer posts, farmer reads; outsider forbidden
r = c.post(f"/api/order/{oid}/messages", json={"text": "Hello, when will it arrive?"})
check("customer sends message", r.status_code == 200 and r.get_json().get("ok"))
mid = r.get_json()["id"]
r = c.get(f"/api/order/{oid}/messages")
data = r.get_json()
check("messages list returns mine flag",
      data["messages"][0]["text"].startswith("Hello") and data["messages"][0]["mine"])

with c.session_transaction() as s:
    s["user_id"] = 1
r = c.get(f"/api/order/{oid}/messages")
msg = r.get_json()["messages"][0]
check("farmer sees message with sender name", bool(msg["sender_name"]))
check("farmer sees message mine=False", msg["mine"] is False)
r = c.post(f"/api/order/{oid}/messages", json={"text": "Out for delivery now!"})
check("farmer replies", r.status_code == 200)

# unrelated customer forbidden
with c.session_transaction() as s:
    s["user_id"] = 9
check("outsider blocked from chat GET", c.get(f"/api/order/{oid}/messages").status_code == 403)
check("outsider blocked from chat POST",
      c.post(f"/api/order/{oid}/messages", json={"text": "spam"}).status_code == 403)

# chat UI markers on farmer page
with c.session_transaction() as s:
    s["user_id"] = 1
page = c.get("/orders").get_data(as_text=True)
for marker in ["chat-toggle", "chat-box", "Chat with buyer"]:
    check("farmer page has " + marker, marker in page)

# 3. DASHBOARD
r = c.get("/dashboard")
check("dashboard renders", r.status_code == 200)
body = r.get_data(as_text=True)
for marker in ["Sales Dashboard", "bar-chart", "Top products", "Recent orders", "Total earnings"]:
    check("dashboard has " + marker, marker in body)

# non-farmer blocked
with c.session_transaction() as s:
    s["user_id"] = 3
check("dashboard blocked for customer", c.get("/dashboard").status_code == 302)

# nav link present for farmers
with c.session_transaction() as s:
    s["user_id"] = 1
check("nav shows Dashboard link", "/dashboard" in c.get("/").get_data(as_text=True))

# cleanup test rows
conn = sqlite3.connect(A.DB_PATH)
conn.execute("DELETE FROM messages WHERE order_id=?", (oid,))
conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
conn.execute("DELETE FROM orders WHERE id=?", (oid,))
conn.execute("DELETE FROM cart_items WHERE product_id=?", (pid,))
conn.execute("DELETE FROM products WHERE id=?", (pid,))
conn.commit()
conn.close()
print("\n" + ("ALL PASS" if ok else "SOME FAILURES"))
