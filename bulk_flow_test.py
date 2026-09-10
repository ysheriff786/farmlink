"""End-to-end tests for FPO + bulk buyer roles and the RFQ (bulk) loop.

Runs against a throwaway database so the real farmmarket.db is untouched.
Usage: py bulk_flow_test.py
"""
import os
import sqlite3
import tempfile

from werkzeug.security import generate_password_hash

import app as appmod

tmpdir = tempfile.mkdtemp(prefix="farmlink_test_")
appmod.DB_PATH = os.path.join(tmpdir, "test.db")
appmod.app.config["TESTING"] = True
appmod.app.config["SECRET_KEY"] = "test"
appmod.init_db()

client = appmod.app.test_client()
fails = []


def check(label, ok):
    if not ok:
        fails.append(label)
        print("FAIL", label)
    else:
        print("OK  ", label)


def con():
    conn = sqlite3.connect(appmod.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


FPROLE = "farmer", "fpo", "customer", "bulk_buyer", "admin"
FPO = {
    "name": "Green Valley FPO", "email": "fpo@test.com", "password": "secret123",
    "role": "fpo", "phone": "9000000001", "contact_person": "Ravi Kumar",
    "registration_id": "FPO/TN/2024/001", "member_count": "42",
    "main_crops": "Tomatoes, Onions, Brinjal", "description": "Agri collective",
    "address": "Main Road", "city": "Coimbatore", "state": "Tamil Nadu",
    "pincode": "641001",
}
BUYER = {
    "name": "FreshMart Hotels", "email": "buyer@test.com", "password": "secret123",
    "role": "bulk_buyer", "phone": "9000000002", "contact_person": "Meena Iyer",
    "buyer_type": "Hotel", "address": "Station Road", "city": "Coimbatore",
    "state": "Tamil Nadu", "pincode": "641001",
}
FARMER = {
    "name": "Demo Farmer", "email": "farmer@test.com", "password": "secret123",
    "role": "farmer", "phone": "9000000003",
}
CUSTOMER = {
    "name": "Demo Customer", "email": "cust@test.com", "password": "secret123",
    "role": "customer", "phone": "9000000004",
}

# ---------- Registration + role-home redirects ----------
r = client.post("/register", data=FPO)
check("fpo registers and redirects to its dashboard",
      r.status_code == 302 and "/fpo/dashboard" in r.headers.get("Location", ""))
r = client.get("/fpo/dashboard")
body = r.get_data(as_text=True)
check("fpo dashboard renders with pending banner", r.status_code == 200 and "pending" in body)
client.get("/logout")

r = client.post("/register", data=BUYER)
check("bulk buyer registers and redirects to its dashboard",
      r.status_code == 302 and "/bulk-buyer/dashboard" in r.headers.get("Location", ""))
r = client.get("/bulk-buyer/dashboard")
body = r.get_data(as_text=True)
check("bulk buyer dashboard renders", r.status_code == 200 and "Bulk Buyer Dashboard" in body)
client.get("/logout")

r = client.post("/register", data=FARMER)
check("farmer redirects to dashboard",
      r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""))
client.get("/logout")

r = client.post("/register", data=CUSTOMER)
check("customer redirects to products",
      r.status_code == 302 and "/products" in r.headers.get("Location", ""))
client.get("/logout")

r = client.post("/register",
                data=dict(BUYER, email="bad@test.com", pincode=""),
                follow_redirects=False)
check("org role without pincode is rejected", r.status_code == 200)
r = client.post("/register", data=dict(BUYER, email="bad2@test.com", role="admin"))
check("admin role cannot be self-registered", r.status_code == 200)

# ---------- DB sanity for new rows ----------
c = con()
fpo_row = c.execute("SELECT * FROM users WHERE email='fpo@test.com'").fetchone()
buyer_row = c.execute("SELECT * FROM users WHERE email='buyer@test.com'").fetchone()
fpo_prof = c.execute(
    "SELECT * FROM fpo_profiles WHERE user_id=?", (fpo_row["id"],)).fetchone()
buyer_prof = c.execute(
    "SELECT * FROM bulk_buyer_profiles WHERE user_id=?", (buyer_row["id"],)).fetchone()
check("fpo_profile row created", fpo_prof is not None and fpo_prof["verification_status"] == "pending")
check("buyer_profile row created",
      buyer_prof is not None and buyer_prof["buyer_type"] == "Hotel")
check("fpo member_count stored", fpo_prof["member_count"] == 42)
check("roles stored", fpo_row["role"] == "fpo" and buyer_row["role"] == "bulk_buyer")
check("role CHECK constraint is wide", "bulk_buyer" in c.execute(
    "SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0])
c.close()

# ---------- FPO can list products (seller role) ----------
client.post("/login", data={"email": "fpo@test.com", "password": "secret123"})
r = client.post("/product/new", data={
    "name": "FPO Tomatoes", "description": "Fresh", "price": "30",
    "mrp": "40", "category": "Vegetables", "unit": "kg", "stock": "100"})
check("fpo creates product", r.status_code == 302)
c = con()
prod = c.execute("SELECT * FROM products WHERE name='FPO Tomatoes'").fetchone()
check("product attributed to fpo account",
      prod is not None and prod["farmer_id"] == fpo_row["id"])
c.close()
client.get("/logout")

# ---------- Bulk buyer posts a requirement ----------
client.post("/login", data={"email": "buyer@test.com", "password": "secret123"})
r = client.post("/bulk-requirement", data={
    "title": "200kg tomatoes weekly", "description": "Steady supply needed",
    "quantity": "200", "unit": "kg", "category": "Vegetables",
    "target_price": "25", "city": "Coimbatore", "pincode": "641001"})
check("bulk requirement published", r.status_code == 302)
c = con()
req = c.execute("SELECT * FROM bulk_requirements WHERE title LIKE '%tomatoes%'").fetchone()
check("requirement stored as Open",
      req is not None and req["status"] == "Open" and req["buyer_id"] == buyer_row["id"])
c.close()
client.get("/logout")

# ---------- FPO builds aggregate supply from a member's produce ----------
c = con()
farmer_row = c.execute("SELECT * FROM users WHERE email='farmer@test.com'").fetchone()
c.close()
client.post("/login", data={"email": "farmer@test.com", "password": "secret123"})
r = client.post("/product/new", data={
    "name": "Tomatoes", "description": "Fresh", "price": "20",
    "mrp": "25", "category": "Vegetables", "unit": "kg", "stock": "120"})
check("farmer lists tomatoes as own product", r.status_code == 302)
client.get("/logout")

client.post("/login", data={"email": "fpo@test.com", "password": "secret123"})
r = client.post("/fpo/member/add", data={"farmer_id": farmer_row["id"]})
check("fpo adds farmer member for supply", r.status_code == 302)
r = client.post("/fpo/supply/new", data={
    "name": "Pooled Tomatoes Supply", "description": "Pooled from members",
    "price": "24", "mrp": "30", "category": "Vegetables", "unit": "kg", "min_qty": "5"})
check("fpo creates aggregate supply", r.status_code == 302)
c = con()
supply = c.execute("SELECT * FROM products WHERE name='Pooled Tomatoes Supply'").fetchone()
tomato_prod = c.execute("SELECT * FROM products WHERE name='Tomatoes'").fetchone()
c.close()
r = client.post(f"/fpo/supply/{supply['id']}/allocation/add",
                data={"product_id": tomato_prod["id"], "committed_qty": "120"})
check("fpo adds allocation to supply", r.status_code == 302)

# ---------- FPO quotes (supply-backed) on the requirement ----------
c = con()
supply = c.execute("SELECT * FROM products WHERE name='Pooled Tomatoes Supply'").fetchone()
c.close()
r = client.get("/fpo/dashboard?tab=requests")
body = r.get_data(as_text=True)
check("fpo sees open requirement in marketplace",
      r.status_code == 200 and "200kg tomatoes weekly" in body)
r = client.post(f"/fpo/quote/{req['id']}",
                data={"supply_id": supply["id"], "price": "22", "quantity": "200",
                      "notes": "Can supply"})
follow = client.get(r.headers.get("Location", "/fpo/dashboard?tab=requests"))
check("quote above available supply is rejected",
      r.status_code == 302 and "available supply" in follow.get_data(as_text=True))
r = client.post(f"/fpo/quote/{req['id']}",
                data={"supply_id": supply["id"], "price": "22", "quantity": "100",
                      "notes": "Can supply"})
check("fpo submits supply-backed quote", r.status_code == 302)
r = client.get("/fpo/dashboard?tab=quotes")
body = r.get_data(as_text=True)
check("fpo quote shows as Pending", "Pending" in body and "22" in body)
c = con()
q = c.execute("SELECT * FROM bulk_quotes WHERE requirement_id=?", (req["id"],)).fetchone()
check("quote is linked to the supply", q is not None and q["supply_id"] == supply["id"])
c.close()
r = client.post(f"/fpo/quote/{req['id']}",
                data={"supply_id": supply["id"], "price": "20", "quantity": "100"})
check("duplicate quote blocked", r.status_code == 302)
c = con()
qtd = c.execute("SELECT COUNT(*) n FROM bulk_quotes WHERE requirement_id=?",
                (req["id"],)).fetchone()["n"]
check("only one quote row exists for this fpo/requirement", qtd == 1)
q = c.execute("SELECT * FROM bulk_quotes WHERE requirement_id=?", (req["id"],)).fetchone()
c.close()
client.get("/logout")

# ---------- Buyers can't be suppliers and vice-versa ----------
client.post("/login", data={"email": "buyer@test.com", "password": "secret123"})
check("bulk buyer blocked from FPO dashboard",
      client.get("/fpo/dashboard").status_code == 302)
check("bulk buyer blocked from adding products",
      client.post("/product/new", data={"name": "x", "price": "1", "stock": "1"}).status_code == 302)
client.get("/logout")

client.post("/login", data={"email": "fpo@test.com", "password": "secret123"})
check("fpo blocked from bulk buyer dashboard",
      client.get("/bulk-buyer/dashboard").status_code == 302)
check("fpo blocked from posting requirements",
      client.post("/bulk-requirement", data={"title": "x", "quantity": "1"}).status_code == 302)
client.get("/logout")

# ---------- Buyer accepts the quote → bulk order + award ----------
client.post("/login", data={"email": "buyer@test.com", "password": "secret123"})
r = client.get("/bulk-buyer/dashboard?tab=quotes")
check("buyer sees received quote",
      r.status_code == 200 and "Green Valley FPO" in r.get_data(as_text=True))
r = client.post(f"/bulk/quote/{q['id']}/accept")
check("buyer accepts quote", r.status_code == 302)
c = con()
bo = c.execute("SELECT * FROM bulk_orders WHERE quote_id=?",
               (q["id"],)).fetchone()
st_req = c.execute("SELECT status FROM bulk_requirements WHERE id=?",
                   (req["id"],)).fetchone()[0]
st_quote = c.execute("SELECT status FROM bulk_quotes WHERE id=?", (q["id"],)).fetchone()[0]
check("bulk order created (Active)",
      bo is not None and bo["status"] == "Active" and bo["total"] == (100 * 22))
check("bulk order keeps supply link",
      bo is not None and bo["supply_id"] == supply["id"] and bo["delivery_address"] != "")
c = con()
cons = c.execute("SELECT * FROM supply_consumption WHERE bulk_order_id=?",
                 (bo["id"],)).fetchone()
check("aggregate supply consumed for bulk order",
      cons is not None and round(cons["qty"], 2) == 100)
check("consumed exactly once",
      c.execute("SELECT COUNT(*) n FROM supply_consumption WHERE bulk_order_id=?",
                (bo["id"],)).fetchone()["n"] == 1)
c.close()
check("requirement awarded", st_req == "Awarded")
check("quote accepted", st_quote == "Accepted")
r = client.get("/bulk-buyer/dashboard?tab=orders")
check("buyer sees bulk order", "Active" in r.get_data(as_text=True))
r = client.post(f"/bulk-order/{bo['id']}/complete")
check("buyer completes bulk order", r.status_code == 302)
c = con()
check("bulk order completed",
      c.execute("SELECT status FROM bulk_orders WHERE id=?", (bo["id"],)).fetchone()[0] == "Completed")
check("requirement closed after completion",
      c.execute("SELECT status FROM bulk_requirements WHERE id=?",
                (req["id"],)).fetchone()[0] == "Closed")
c.close()
client.get("/logout")

# ---------- Admin verification ----------
c = con()
c.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?,?,?,?)",
          ("Admin", "admin@test.com", generate_password_hash("adminpass"), "admin"))
c.commit()
c.close()
client.post("/login", data={"email": "admin@test.com", "password": "adminpass"})
r = client.get("/admin")
body = r.get_data(as_text=True)
check("admin page lists FPO account", "Green Valley FPO" in body)
check("admin page lists bulk buyer account", "FreshMart Hotels" in body)
r = client.post(f"/admin/fpo/{fpo_row['id']}/verify")
check("admin verifies fpo", r.status_code == 302)
r = client.post(f"/admin/bulk/{buyer_row['id']}/verify")
check("admin verifies bulk buyer", r.status_code == 302)
r = client.get("/admin")
check("admin stats show 0 pending", "Pending verification" in r.get_data(as_text=True))
client.get("/logout")

client.post("/login", data={"email": "buyer@test.com", "password": "secret123"})
r = client.get("/bulk-buyer/dashboard?tab=find")
check("verified FPOs appear in supplier directory",
      "Green Valley FPO" in r.get_data(as_text=True))
client.get("/logout")

client.post("/login", data={"email": "fpo@test.com", "password": "secret123"})
r = client.get("/fpo/dashboard")
check("fpo dashboard no longer shows pending warning",
      "pending" not in r.get_data(as_text=True))
client.get("/logout")

# ---------- FPO members ----------
c = con()
farmer_row = c.execute("SELECT * FROM users WHERE email='farmer@test.com'").fetchone()
c.close()
client.post("/login", data={"email": "fpo@test.com", "password": "secret123"})
r = client.get("/fpo/dashboard?tab=members")
check("members tab lists available farmers", "Demo Farmer" in r.get_data(as_text=True))
r = client.post("/fpo/member/add", data={"farmer_id": farmer_row["id"]})
check("fpo adds farmer member", r.status_code == 302)
c = con()
mem = c.execute("SELECT * FROM fpo_members WHERE fpo_id=? AND farmer_id=?",
                (fpo_row["id"], farmer_row["id"])).fetchone()
check("member row stored as approved", mem is not None and mem["status"] == "approved")
mid = mem["id"]
c.close()
r = client.post(f"/fpo/member/{mid}/remove")
check("fpo removes member", r.status_code == 302)
c = con()
check("member row deleted",
      c.execute("SELECT COUNT(*) n FROM fpo_members WHERE id=?", (mid,)).fetchone()["n"] == 0)
c.close()
client.get("/logout")

# ---------- Registration validation for duplicate play ----------
client.post("/register", data=FPO)
client.get("/logout")
client.post("/login", data={"email": "fpo@test.com", "password": "secret123"})
r = client.get("/fpo/dashboard")
check("fpo login redirects to fpo dashboard after re-login",
      r.status_code == 200)


print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
import sys

sys.exit(1 if fails else 0)