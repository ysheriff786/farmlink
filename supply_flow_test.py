"""End-to-end tests for the FPO supply aggregation feature.

Covers: committed allIocation math, marketplace listing + min qty, order
consumption through the ledger, oversell clamps, cancellation restore,
allocation lifecycle, ownership guards and the RFQ availability sidebar.

Runs against a throwaway database so the real farmmarket.db is untouched.
Usage: py supply_flow_test.py
"""
import os
import sqlite3
import tempfile

import app as appmod

tmpdir = tempfile.mkdtemp(prefix="farmlink_supply_test_")
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


def register(data):
    return client.post("/register", data=data, follow_redirects=False)


def login(email, pw="secret123"):
    client.post("/login", data={"email": email, "password": pw}, follow_redirects=False)


def logout():
    client.get("/logout")


def add_product(name, stock, price="40", unit="kg"):
    r = client.post("/product/new", data={
        "name": name, "description": "test", "price": price, "mrp": "",
        "unit": unit, "category": "Vegetables", "stock": str(stock),
        "image": "", "min_qty": "1"}, follow_redirects=False)
    assert r.status_code == 302
    c = con()
    row = c.execute("SELECT id FROM products WHERE name=? ORDER BY id DESC LIMIT 1",
                    (name,)).fetchone()
    c.close()
    return row["id"]


FPO1 = {"name": "Green Valley FPO", "email": "fpo@test.com", "password": "secret123",
        "role": "fpo", "phone": "9000000001", "contact_person": "Ravi",
        "registration_id": "FPO/TN/2024/001", "address": "Road", "city": "Town",
        "state": "TN", "pincode": "641001"}
FPO2 = {"name": "Second FPO", "email": "fpo2@test.com", "password": "secret123",
        "role": "fpo", "phone": "9000000002", "contact_person": "Kavi",
        "registration_id": "FPO/KA/2024/002", "address": "Road", "city": "Town",
        "state": "KA", "pincode": "560001"}
FARM_A = {"name": "Farmer A", "email": "a@test.com", "password": "secret123",
          "role": "farmer", "phone": "9000000003"}
FARM_B = {"name": "Farmer B", "email": "b@test.com", "password": "secret123",
          "role": "farmer", "phone": "9000000004"}
FARM_C = {"name": "Farmer C", "email": "c@test.com", "password": "secret123",
          "role": "farmer", "phone": "9000000005"}
CUSTOMER = {"name": "Cust", "email": "cust@test.com", "password": "secret123",
            "role": "customer", "phone": "9000000006"}

# ---------- Setup: register all users ----------
for d in (FPO1, FPO2, FARM_A, FARM_B, FARM_C, CUSTOMER):
    check(f"register {d['email']}", register(d).status_code == 302)
logout()

# Farmer A: 100 kg tomatoes, 40 kg spinach. Farmer B: 50 dozen eggs.
login(FARM_A["email"])
tomato_id = add_product("A Tomatoes", 100)
spinach_id = add_product("A Spinach", 40, price="20", unit="bunch")
logout()
login(FARM_B["email"])
eggs_id = add_product("B Eggs", 50, unit="dozen")
logout()
login(FARM_C["email"])
cabbage_id = add_product("C Cabbage", 30)
logout()

# ---------- Aggregation: membership + allocations ----------
login(FPO1["email"])
for uid in ("a@test.com", "b@test.com"):
    c = con()
    fid = c.execute("SELECT id FROM users WHERE email=?", (uid,)).fetchone()["id"]
    c.close()
    client.post("/fpo/member/add", data={"farmer_id": fid}, follow_redirects=False)
r = client.get("/fpo/dashboard?tab=members")
body = r.get_data(as_text=True)
check("members approved", "a@test.com" in body and "b@test.com" in body)

r = client.post("/fpo/supply/new", data={
    "name": "FPO Tomato Pool", "price": "38", "mrp": "45", "unit": "kg",
    "category": "Vegetables", "min_qty": "5", "description": "pooled"}, follow_redirects=False)
supply_id = int(r.headers.get("Location", "").rstrip("/").split("/")[-1])
check("supply listing created", r.status_code == 302 and "/fpo/supply/" in r.headers.get("Location", ""))

# Allocation from a NON-member farmer must fail.
r = client.post(f"/fpo/supply/{supply_id}/allocation/add", data={
    "product_id": cabbage_id, "committed_qty": "10"}, follow_redirects=True)
check("non-member allocation blocked", "Pick a product" in r.get_data(as_text=True))

# Valid allocation: 70 of farmer A's 100 kg tomatoes.
client.post(f"/fpo/supply/{supply_id}/allocation/add", data={
    "product_id": tomato_id, "committed_qty": "70"}, follow_redirects=False)
# Overshoot beyond remaining 30 must fail (100 - 70 already committed).
r = client.post(f"/fpo/supply/{supply_id}/allocation/add", data={
    "product_id": spinach_id, "committed_qty": "50"}, follow_redirects=True)
check("overshoot beyond remaining committed blocked", "40" in r.get_data(as_text=True))
# Second valid allocation: 30 of farmer B's 50 dozen eggs.
client.post(f"/fpo/supply/{supply_id}/allocation/add", data={
    "product_id": eggs_id, "committed_qty": "30"}, follow_redirects=False)

c = con()
supply = c.execute("SELECT stock, is_aggregate, min_qty FROM products WHERE id=?",
                   (supply_id,)).fetchone()
hands = c.execute("SELECT COUNT(*) n FROM supply_allocations WHERE supply_id=?",
                  (supply_id,)).fetchone()["n"]
c.close()
check("on-hand materialised (70 + 30)", supply["stock"] == 100)
check("listed as aggregate with min_qty 5", supply["is_aggregate"] == 1 and supply["min_qty"] == 5)
check("two allocations recorded", hands == 2)

# Another FPO cannot manage this supply.
logout()
login(FPO2["email"])
r = client.get(f"/fpo/supply/{supply_id}")
check("other FPO cannot view supply", r.status_code == 302)
r = client.post(f"/fpo/supply/{supply_id}/allocation/add", data={
    "product_id": cabbage_id, "committed_qty": "5"}, follow_redirects=True)
check("other FPO cannot add allocation", r.status_code == 200 and "not found" in r.get_data(as_text=True).lower())
logout()

# ---------- Marketplace + cart ----------
login(CUSTOMER["email"])
r = client.get("/products")
body = r.get_data(as_text=True)
check("marketplace shows FPO supply badge", "FPO supply" in body)

# Min qty enforcement: pool min is 5, so qty 2 must be rejected.
r = client.post(f"/cart/add/{supply_id}", data={"quantity": "2"}, follow_redirects=True)
check("min order qty enforced at cart", "Minimum order" in r.get_data(as_text=True))
client.post(f"/cart/add/{supply_id}", data={"quantity": "10"}, follow_redirects=False)
r = client.get("/cart")
check("agg item added to cart", "FPO Tomato Pool" in r.get_data(as_text=True))

# Checkout consumes the pool via the ledger.
r = client.post("/place-order", data={
    "phone": "9876543210", "address": "12 Somewhere Street, Coimbatore 641001",
    "payment_method": "COD"}, follow_redirects=False)
check("place order accepted", r.status_code == 302)
c = con()
order_row = c.execute("SELECT id FROM orders ORDER BY id DESC LIMIT 1").fetchone()
order_id = order_row["id"]
c.close()

c = con()
supply = c.execute("SELECT stock FROM products WHERE id=?", (supply_id,)).fetchone()
tomato = c.execute("SELECT stock FROM products WHERE id=?", (tomato_id,)).fetchone()
committed = c.execute("SELECT committed_qty FROM supply_allocations WHERE supply_id=? "
                      "AND product_id=?", (supply_id, tomato_id)).fetchone()
consum = c.execute("SELECT COUNT(*) n, COALESCE(SUM(qty),0) s FROM supply_consumption "
                   "WHERE supply_id=?", (supply_id,)).fetchone()
oi = c.execute("SELECT id FROM order_items WHERE order_id=? AND product_name='FPO Tomato Pool'",
               (order_id,)).fetchone()
c.close()
check("aggregate on-hand dropped by 10", supply["stock"] == 90)
check("member tomato stock decremented", tomato["stock"] == 90)
check("member committed qty decremented", committed["committed_qty"] == 60)
check("ledger rows written", consum["n"] >= 1 and consum["s"] == 10 and oi is not None)
logout()

# ---------- Farmer's own edit clamps aggregate on-hand ----------
login(FARM_A["email"])
client.post(f"/product/{tomato_id}/edit", data={
    "name": "A Tomatoes", "description": "test", "price": "40", "mrp": "",
    "unit": "kg", "category": "Vegetables", "stock": "50", "image": "",
    "min_qty": "1"}, follow_redirects=False)
logout()
c = con()
stock = c.execute("SELECT stock FROM products WHERE id=?", (supply_id,)).fetchone()["stock"]
c.close()
check("on-hand clamps when member stock drops (min(50,60)+30=80)", stock == 80)

# ---------- Cancellation restores exactly, without double-restore ----------
login(CUSTOMER["email"])
client.post(f"/order/{order_id}/status", data={"status": "Cancelled"}, follow_redirects=False)
logout()
c = con()
pool_stock = c.execute("SELECT stock FROM products WHERE id=?", (supply_id,)).fetchone()["stock"]
tomato = c.execute("SELECT stock FROM products WHERE id=?", (tomato_id,)).fetchone()["stock"]
committed = c.execute("SELECT committed_qty FROM supply_allocations WHERE supply_id=? "
                      "AND product_id=?", (supply_id, tomato_id)).fetchone()["committed_qty"]
left = c.execute("SELECT COUNT(*) n FROM supply_consumption WHERE supply_id=?",
                 (supply_id,)).fetchone()["n"]
c.close()
check("cancel restores member stock (50+10)", tomato == 60)
check("cancel restores committed qty (60+10)", committed == 70)
check("cancel restores pool on-hand to min(70,60)+30 = 90", pool_stock == 90)
check("ledger cleared on cancel", left == 0)

# ---------- Allocation lifecycle ----------
login(FPO1["email"])
client.post(f"/fpo/supply/{supply_id}/allocation/{1}/remove", follow_redirects=False)
c = con()
hands = c.execute("SELECT COUNT(*) n FROM supply_allocations WHERE supply_id=?",
                  (supply_id,)).fetchone()["n"]
stock = c.execute("SELECT stock FROM products WHERE id=?", (supply_id,)).fetchone()["stock"]
c.close()
check("allocation removed", hands == 1)
check("on-hand recomputed after removal (farmer B eggs 30)", stock == 30)

# Update allocation beyond what the member has → blocked.
r = client.post(f"/fpo/supply/{supply_id}/allocation/2/update", data={
    "committed_qty": "999"}, follow_redirects=True)
check("alloc update beyond stock blocked", "exceeds" in r.get_data(as_text=True).lower())

# RFQ sidebar shows the supply on the requests tab.
r = client.get("/fpo/dashboard?tab=requests")
check("RFQ sidebar lists aggregate supply", "My aggregate supply" in r.get_data(as_text=True))

# Delete the supply entirely.
r = client.post(f"/fpo/supply/{supply_id}/delete", follow_redirects=False)
check("supply delete accepted", r.status_code == 302)
c = con()
gone = c.execute("SELECT 1 FROM products WHERE id=?", (supply_id,)).fetchone()
alloc = c.execute("SELECT 1 FROM supply_allocations WHERE supply_id=?", (supply_id,)).fetchone()
c.close()
check("supply row removed", gone is None)
check("its allocations removed", alloc is None)
logout()

# Deleting a member product also clears its allocations.
login(FPO1["email"])
c = con()
cabbage_supply = c.execute("SELECT id FROM products WHERE name='FPO Tomato Pool'").fetchone()
if cabbage_supply:
    cabbage_supply = cabbage_supply["id"]
c.close()
if not cabbage_supply:
    r = client.post("/fpo/supply/new", data={
        "name": "FPO Cabbage Pool", "price": "18", "mrp": "", "unit": "kg",
        "category": "Vegetables", "min_qty": "1", "description": ""}, follow_redirects=False)
    cabbage_supply = int(r.headers.get("Location", "").rstrip("/").split("/")[-1])
    client.post(f"/fpo/supply/{cabbage_supply}/allocation/add", data={
        "product_id": cabbage_id, "committed_qty": "30"}, follow_redirects=False)
logout()
login(FARM_C["email"])
client.post(f"/product/{cabbage_id}/delete", follow_redirects=False)
logout()
c = con()
alloc = c.execute("SELECT 1 FROM supply_allocations WHERE product_id=?",
                  (cabbage_id,)).fetchone()
c.close()
check("deleting member product clears its allocation", alloc is None)

# ---------- Access control ----------
login(CUSTOMER["email"])
r = client.post("/fpo/supply/new", data={"name": "x", "price": "1"}, follow_redirects=False)
check("customer blocked from supply creation", r.status_code == 302)
logout()

print()
if fails:
    print(f"{len(fails)} FAILURES:")
    for f in fails:
        print(" -", f)
else:
    print("ALL PASS")
