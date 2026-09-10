import random

import app as A

A.init_db()
c = A.app.test_client()
ok = True


def check(label, cond):
    global ok
    print(("OK  " if cond else "FAIL") + " " + label)
    ok = ok and cond


# find an existing order owned by customer 3
import sqlite3

oid = sqlite3.connect(A.DB_PATH).execute(
    "SELECT id FROM orders WHERE customer_id=3 ORDER BY id DESC LIMIT 1").fetchone()[0]

# 1. GUEST: redirected to login, page never opens
r = c.get(f"/track?order={oid}", follow_redirects=False)
check("guest /track redirects to login", r.status_code == 302
      and "/login" in r.headers.get("Location", ""))

# 2. GUEST: status API locked
check("guest status API -> 401", c.get(f"/api/order/{oid}/status").status_code == 401)

# 3. OTHER CUSTOMER: denied
with c.session_transaction() as s:
    s["user_id"] = 9  # a different customer
body = c.get(f"/track?order={oid}").get_data(as_text=True)
check("non-owner customer blocked", "belongs to a different account" in body
      and "timeline" not in body)
check("non-owner status API -> 403",
      c.get(f"/api/order/{oid}/status").status_code == 403)

# 4. OWNER: can track
with c.session_transaction() as s:
    s["user_id"] = 3
r = c.get(f"/track?order={oid}")
body = r.get_data(as_text=True)
check("owner can track own order", r.status_code == 200 and "timeline" in body)
check("owner status API -> 200", c.get(f"/api/order/{oid}/status").status_code == 200)

# 5. FARMER with items in order: can track (farmer 1 owns products of early orders)
farmer_has = sqlite3.connect(A.DB_PATH).execute(
    "SELECT oi.order_id FROM order_items oi WHERE oi.farmer_id=1 "
    "AND oi.order_id=? ", (oid,)).fetchone()
if farmer_has:
    with c.session_transaction() as s:
        s["user_id"] = 1
    check("farmer in order can track",
          c.get(f"/track?order={oid}").status_code == 200)
else:
    print("NOTE farmer1 not in this order; skipped")

# 6. OPEN REDIRECT: //evil.com must not be used as next
email = f"sec{random.randint(10000, 99999)}@test.com"
c.get("/logout")
c.post("/register", data={"name": "Sec Tester", "email": email, "password": "secret1",
        "role": "customer", "phone": "9000000000"})
c.get("/logout")
r = c.post("/login", data={"email": email, "password": "secret1", "next": "//evil.com"},
           follow_redirects=False)
loc = r.headers.get("Location", "")
check("open redirect // blocked", not loc.startswith("//"))
r = c.post("/login", data={"email": email, "password": "secret1", "next": "/orders"},
           follow_redirects=False)
check("normal /next still works", r.headers.get("Location", "") == "/orders")

# 7. fresh registered user cannot see others' orders via track
with c.session_transaction() as s:
    pass  # session already holds new user from login
body = c.get(f"/track?order={oid}").get_data(as_text=True)
check("new user blocked from others' order", "timeline" not in body)

print("\n" + ("ALL PASS" if ok else "SOME FAILURES"))
