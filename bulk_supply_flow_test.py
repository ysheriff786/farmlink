"""End-to-end tests connecting FPO aggregate supply to the bulk RFQ/quote/order loop.

Covers: matching, quote-time availability checks, atomic accept with reservation,
double-sell prevention, cancel-restore, competing-quote rejection, legacy quotes,
partial fulfillment, traceability. Runs on a throwaway DB.
Usage: py bulk_supply_flow_test.py
"""
import os
import sqlite3
import tempfile

from werkzeug.security import generate_password_hash

import app as appmod

tmpdir = tempfile.mkdtemp(prefix="farmlink_bulk_supply_test_")
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
        print("OK  ", label.encode("ascii", "replace").decode())


def con():
    conn = sqlite3.connect(appmod.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def reg(data):
    return client.post("/register", data=data, follow_redirects=False)


FPO = {
    "name": "Agri Valley FPO", "email": "agrifpo@test.com", "password": "secret123",
    "role": "fpo", "phone": "9000000101", "contact_person": "Ganesh Rao",
    "registration_id": "FPO/KA/2024/002", "member_count": "25",
    "main_crops": "Tomatoes, Onion", "description": "Farmer collective",
    "address": "Farm Road", "city": "Mandya", "state": "Karnataka", "pincode": "571401",
}
BUYER_A = {
    "name": "Spice Route Hotels", "email": "buyera@test.com", "password": "secret123",
    "role": "bulk_buyer", "phone": "9000000102", "contact_person": "Kavi D",
    "buyer_type": "Hotel", "address": "MG Road", "city": "Bengaluru",
    "state": "Karnataka", "pincode": "560001",
}
BUYER_B = {
    "name": "Namma Foods", "email": "buyerb@test.com", "password": "secret123",
    "role": "bulk_buyer", "phone": "9000000103", "contact_person": "Ravi B",
    "buyer_type": "Restaurant", "address": "Residency Road", "city": "Bengaluru",
    "state": "Karnataka", "pincode": "560025",
}
FARMER = {
    "name": "Ramu Tomato", "email": "ramu@test.com", "password": "secret123",
    "role": "farmer", "phone": "9000000104",
}

check("fpo registers", 302 == reg(FPO).status_code)
check("buyer A registers", 302 == reg(BUYER_A).status_code)
check("buyer B registers", 302 == reg(BUYER_B).status_code)
check("farmer registers", 302 == reg(FARMER).status_code)
client.get("/logout")

# ---------- ids + ad-hoc verification ----------
c = con()
fpo_id = c.execute("SELECT id FROM users WHERE email='agrifpo@test.com'").fetchone()["id"]
buyer_a = c.execute("SELECT id FROM users WHERE email='buyera@test.com'").fetchone()["id"]
buyer_b = c.execute("SELECT id FROM users WHERE email='buyerb@test.com'").fetchone()["id"]
farmer_id = c.execute("SELECT id FROM users WHERE email='ramu@test.com'").fetchone()["id"]
c.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?,?,?,?)",
          ("Second FPO", "fpo2@test.com", generate_password_hash("secret123"), "fpo"))
fpo2_id = c.execute("SELECT id FROM users WHERE email='fpo2@test.com'").fetchone()["id"]
c.execute("UPDATE fpo_profiles SET verification_status='verified' WHERE user_id=?",
          (fpo_id,))
c.execute("UPDATE bulk_buyer_profiles SET verification_status='verified' WHERE user_id=?",
          (buyer_a,))
c.commit()

# ---------- supply built from a member's produce (100 kg tomatoes) ----------
client.post("/login", data={"email": "ramu@test.com", "password": "secret123"})
r = client.post("/product/new", data={
    "name": "Tomatoes", "description": "Fresh", "price": "20",
    "mrp": "25", "category": "Vegetables", "unit": "kg", "stock": "100"})
check("farmer lists tomatoes", r.status_code == 302)
client.get("/logout")

client.post("/login", data={"email": "agrifpo@test.com", "password": "secret123"})
client.post("/fpo/member/add", data={"farmer_id": farmer_id})
r = client.post("/fpo/supply/new", data={
    "name": "Pooled Tomatoes", "description": "Tomatoes pooled from members",
    "price": "24", "mrp": "30", "category": "Vegetables", "unit": "kg", "min_qty": "10"})
check("fpo creates aggregate supply", r.status_code == 302)
c = con()
supply = c.execute("SELECT * FROM products WHERE name='Pooled Tomatoes'").fetchone()
tomatoes_prod = c.execute("SELECT * FROM products WHERE name='Tomatoes'").fetchone()
c.close()
r = client.post(f"/fpo/supply/{supply['id']}/allocation/add",
                data={"product_id": tomatoes_prod["id"], "committed_qty": "100"})
check("fpo pools 100 kg into the supply", r.status_code == 302)
c = con()
check("aggregate on hand is 100 kg",
      round(appmod._aggregate_on_hand(c, supply["id"])) == 100)
check("aggregate listing stock refreshed to 100",
      c.execute("SELECT stock FROM products WHERE id=?",
                (supply["id"],)).fetchone()[0] == 100)
c.close()

# ---------- matching rules ----------
onion_req = {"title": "500 kg onions weekly", "category": "Vegetables", "unit": "kg"}
tomato_req = {"title": "150 kg tomatoes", "category": "Vegetables", "unit": "kg"}
with appmod.app.app_context():
    check("onion requirement does NOT match tomato supply",
          not appmod._matching_supplies(fpo_id, onion_req))
    tomato_matches = appmod._matching_supplies(fpo_id, tomato_req)
check("tomato requirement matches the pooled supply",
      len(tomato_matches) == 1 and round(tomato_matches[0]["on_hand"]) == 100)

# ---------- R1: quote-time over-supply is rejected, then partial quote ----------
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
r = client.post("/bulk-requirement", data={
    "title": "150 kg tomatoes", "description": "For festival week",
    "quantity": "150", "unit": "kg", "category": "Vegetables",
    "target_price": "26", "city": "Bengaluru", "pincode": "560001"})
check("buyer A posts 150 kg tomato requirement", r.status_code == 302)
client.get("/logout")
c = con()
r1 = c.execute("SELECT * FROM bulk_requirements WHERE title='150 kg tomatoes'").fetchone()
c.close()

client.post("/login", data={"email": "agrifpo@test.com", "password": "secret123"})
body = client.get("/fpo/dashboard?tab=requests").get_data(as_text=True)
check("fpo requests tab flags partial fulfillment",
      "150 kg tomatoes" in body and "Partial fulfillment" in body)
r = client.post(f"/fpo/quote/{r1['id']}",
                data={"supply_id": supply["id"], "price": "24", "quantity": "150"})
check("quote for more than available supply is rejected at quote time",
      r.status_code == 302)
follow = client.get(r.headers.get("Location", "/fpo/dashboard?tab=requests"))
check("rejection tells the fpo its available supply",
      "available supply is 100 kg" in follow.get_data(as_text=True))
r = client.post(f"/fpo/quote/{r1['id']}",
                data={"supply_id": supply["id"], "price": "24", "quantity": "100",
                      "notes": "Half the requirement"})
check("fpo quotes 100 kg against the supply", r.status_code == 302)
c = con()
q1 = c.execute("SELECT * FROM bulk_quotes WHERE requirement_id=? AND fpo_id=?",
               (r1["id"], fpo_id)).fetchone()
check("quote recorded with supply link",
      q1 is not None and q1["supply_id"] == supply["id"] and round(q1["quantity"]) == 100)
check("only one quote per fpo per requirement",
      c.execute("SELECT COUNT(*) n FROM bulk_quotes WHERE requirement_id=? AND fpo_id=?",
                (r1["id"], fpo_id)).fetchone()["n"] == 1)
c.close()

# ---------- buyer A accepts → order + reservation + traceability ----------
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
r = client.get("/bulk-buyer/dashboard?tab=quotes")
body = r.get_data(as_text=True)
check("buyer sees quote marked as partial coverage",
      "150 kg tomatoes" in body and "Covers" in body)
r = client.post(f"/bulk/quote/{q1['id']}/accept")
check("buyer accepts the supply-backed quote", r.status_code == 302)
c = con()
o1 = c.execute("SELECT * FROM bulk_orders WHERE quote_id=?", (q1["id"],)).fetchone()
check("bulk order created, Active, supply-linked, with delivery address",
      o1 is not None and o1["status"] == "Active"
      and o1["supply_id"] == supply["id"] and o1["delivery_address"] == "MG Road")
check("requirement awarded", c.execute(
    "SELECT status FROM bulk_requirements WHERE id=?",
    (r1["id"],)).fetchone()[0] == "Awarded")
check("member stock reduced (100 -> 0)", c.execute(
    "SELECT stock FROM products WHERE id=?",
    (tomatoes_prod["id"],)).fetchone()[0] == 0)
check("aggregate listing stock reduced to 0", c.execute(
    "SELECT stock FROM products WHERE id=?", (supply["id"],)).fetchone()[0] == 0)
rows = c.execute("SELECT * FROM supply_consumption WHERE bulk_order_id=?", (o1["id"],)).fetchall()
check("one consumption row created for the bulk order",
      len(rows) == 1 and round(rows[0]["qty"]) == 100)
c.close()
r = client.get("/bulk-buyer/dashboard?tab=orders")
check("buyer order list does not leak farmer names",
      "Ramu Tomato" not in r.get_data(as_text=True))
client.get("/logout")

# ---------- FPO traceability view ----------
client.post("/login", data={"email": "agrifpo@test.com", "password": "secret123"})
body = client.get("/fpo/dashboard?tab=orders").get_data(as_text=True)
check("fpo orders tab shows farmer contribution per order", "Ramu Tomato" in body)
body = client.get(f"/fpo/supply/{supply['id']}").get_data(as_text=True)
check("supply ledger marks bulk consumption", "bulk" in body and "Ramu Tomato" in body)
client.get("/logout")

# ---------- after consume: no more quoting possible ----------
client.post("/login", data={"email": "buyerb@test.com", "password": "secret123"})
r = client.post("/bulk-requirement", data={
    "title": "20 kg tomatoes", "description": "Small order",
    "quantity": "20", "unit": "kg", "category": "Vegetables",
    "target_price": "30", "city": "Bengaluru", "pincode": "560025"})
client.get("/logout")
c = con()
r2 = c.execute("SELECT * FROM bulk_requirements WHERE title='20 kg tomatoes'").fetchone()
c.close()
client.post("/login", data={"email": "agrifpo@test.com", "password": "secret123"})
r = client.post(f"/fpo/quote/{r2['id']}",
                data={"supply_id": supply["id"], "price": "24", "quantity": "20"})
follow = client.get(r.headers.get("Location", "/fpo/dashboard?tab=requests"))
check("no quotes possible once supply is fully consumed",
      "available supply is 0 kg" in follow.get_data(as_text=True))
client.get("/logout")

# ---------- buyer A cancels → supply restored, requirement reopened ----------
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
r = client.post(f"/bulk-order/{o1['id']}/cancel")
check("buyer cancels the bulk order", r.status_code == 302)
client.get("/logout")
c = con()
check("cancelled order status", c.execute(
    "SELECT status FROM bulk_orders WHERE id=?", (o1["id"],)).fetchone()[0] == "Cancelled")
check("supply restored to 100", c.execute(
    "SELECT stock FROM products WHERE id=?", (supply["id"],)).fetchone()[0] == 100)
check("member stock restored to 100", c.execute(
    "SELECT stock FROM products WHERE id=?",
    (tomatoes_prod["id"],)).fetchone()[0] == 100)
check("consumption rows removed on cancel",
      c.execute("SELECT COUNT(*) n FROM supply_consumption WHERE bulk_order_id=?",
                (o1["id"],)).fetchone()["n"] == 0)
check("requirement reopened after cancel", c.execute(
    "SELECT status FROM bulk_requirements WHERE id=?",
    (r1["id"],)).fetchone()[0] == "Open")
c.close()

# ---------- legacy quote without supply link still works, no consumption ----------
client.post("/login", data={"email": "buyerb@test.com", "password": "secret123"})
r = client.post("/bulk-requirement", data={
    "title": "60 kg tomatoes legacy", "description": "Old-style deal",
    "quantity": "60", "unit": "kg", "category": "Vegetables"})
client.get("/logout")
c = con()
r_legacy = c.execute(
    "SELECT * FROM bulk_requirements WHERE title='60 kg tomatoes legacy'").fetchone()
c.execute("INSERT INTO bulk_quotes (requirement_id, fpo_id, price, quantity, notes) "
          "VALUES (?,?,?,?,?)",
          (r_legacy["id"], fpo2_id, 25, 60, "legacy quote"))
legacy_q = c.execute("SELECT id FROM bulk_quotes WHERE requirement_id=? AND fpo_id=?",
                     (r_legacy["id"], fpo2_id)).fetchone()["id"]
c.commit()
c.close()
client.post("/login", data={"email": "buyerb@test.com", "password": "secret123"})
check("buyer accepts legacy quote", 302 == client.post(
    f"/bulk/quote/{legacy_q}/accept").status_code)
client.get("/logout")
c = con()
legacy_o = c.execute("SELECT * FROM bulk_orders WHERE quote_id=?", (legacy_q,)).fetchone()
check("legacy order created without supply link",
      legacy_o is not None and legacy_o["supply_id"] is None)
check("legacy order consumed nothing",
      c.execute("SELECT COUNT(*) n FROM supply_consumption WHERE bulk_order_id=?",
                (legacy_o["id"],)).fetchone()["n"] == 0)
check("supply untouched by legacy order", c.execute(
    "SELECT stock FROM products WHERE id=?", (supply["id"],)).fetchone()[0] == 100)
c.close()

# ---------- competing quotes: accepting one rejects the other ----------
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
r = client.post("/bulk-requirement", data={
    "title": "70 kg tomatoes canteen", "description": "Canteen batch",
    "quantity": "70", "unit": "kg", "category": "Vegetables",
    "target_price": "25", "city": "Bengaluru", "pincode": "560001"})
check("buyer A posts canteen requirement", r.status_code == 302)
client.get("/logout")
c = con()
r5 = c.execute(
    "SELECT * FROM bulk_requirements WHERE title='70 kg tomatoes canteen'").fetchone()
c.close()
client.post("/login", data={"email": "agrifpo@test.com", "password": "secret123"})
check("fpo quotes on fresh requirement", 302 == client.post(
    f"/fpo/quote/{r5['id']}",
    data={"supply_id": supply["id"], "price": "24", "quantity": "70"}).status_code)
c = con()
q_comp = c.execute("SELECT id FROM bulk_quotes WHERE requirement_id=? AND fpo_id=?",
                   (r5["id"], fpo_id)).fetchone()["id"]
c.execute("INSERT INTO bulk_quotes (requirement_id, fpo_id, price, quantity, notes, supply_id) "
          "VALUES (?,?,?,?,?,NULL)",
          (r5["id"], fpo2_id, 23, 70, "competing quote"))
c.commit()
f_comp = c.execute("SELECT id FROM bulk_quotes WHERE requirement_id=? AND fpo_id=?",
                   (r5["id"], fpo2_id)).fetchone()["id"]
c.close()
client.get("/logout")
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
check("buyer accepts the fpo quote", 302 == client.post(
    f"/bulk/quote/{q_comp}/accept").status_code)
client.get("/logout")
c = con()
check("requirement awarded",
      c.execute("SELECT status FROM bulk_requirements WHERE id=?",
                (r5["id"],)).fetchone()[0] == "Awarded")
check("competitor quote rejected",
      c.execute("SELECT status FROM bulk_quotes WHERE id=?", (f_comp,)).fetchone()[0] == "Rejected")
check("supply consumed to 30 for the winning quote", c.execute(
    "SELECT stock FROM products WHERE id=?", (supply["id"],)).fetchone()[0] == 30)
c.close()

# ---------- cancel frees supply for the next round ----------
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
c = con()
comp_o = c.execute("SELECT id FROM bulk_orders WHERE quote_id=?", (q_comp,)).fetchone()["id"]
c.close()
check("buyer cancels competing-order so supply is freed", 302 == client.post(
    f"/bulk-order/{comp_o}/cancel").status_code)
client.get("/logout")
c = con()
check("supply restored to 100", c.execute(
    "SELECT stock FROM products WHERE id=?", (supply["id"],)).fetchone()[0] == 100)
c.close()

# ---------- double-sell prevention at accept time ----------
client.post("/login", data={"email": "buyerb@test.com", "password": "secret123"})
r = client.post("/bulk-requirement", data={
    "title": "30 kg tomatoes pickup", "description": "Small batch",
    "quantity": "30", "unit": "kg", "category": "Vegetables"})
client.get("/logout")
c = con()
r3 = c.execute("SELECT * FROM bulk_requirements WHERE title='30 kg tomatoes pickup'").fetchone()
c.close()

client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
r = client.post("/bulk-requirement", data={
    "title": "90 kg tomatoes kitchen", "description": "Big batch",
    "quantity": "90", "unit": "kg", "category": "Vegetables"})
client.get("/logout")
c = con()
r4 = c.execute("SELECT * FROM bulk_requirements WHERE title='90 kg tomatoes kitchen'").fetchone()
c.close()

client.post("/login", data={"email": "agrifpo@test.com", "password": "secret123"})
check("quote 30 kg passes quote-time check", 302 == client.post(
    f"/fpo/quote/{r3['id']}",
    data={"supply_id": supply["id"], "price": "24", "quantity": "30"}).status_code)
check("quote 90 kg passes quote-time check", 302 == client.post(
    f"/fpo/quote/{r4['id']}",
    data={"supply_id": supply["id"], "price": "24", "quantity": "90"}).status_code)
client.get("/logout")
c = con()
q3 = c.execute("SELECT id FROM bulk_quotes WHERE requirement_id=? AND fpo_id=?",
               (r3["id"], fpo_id)).fetchone()["id"]
q4 = c.execute("SELECT id FROM bulk_quotes WHERE requirement_id=? AND fpo_id=?",
               (r4["id"], fpo_id)).fetchone()["id"]
c.close()

client.post("/login", data={"email": "buyerb@test.com", "password": "secret123"})
check("buyer B accepts 30 kg quote", 302 == client.post(
    f"/bulk/quote/{q3}/accept").status_code)
client.get("/logout")
c = con()
check("30 kg order consumed supply (on hand 70)",
      c.execute("SELECT stock FROM products WHERE id=?",
                (supply["id"],)).fetchone()[0] == 70)
c.close()
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
r = client.post(f"/bulk/quote/{q4}/accept")
check("accepting 90 kg with only 70 left is refused",
      r.status_code == 302)
follow = client.get(r.headers.get("Location", "/bulk-buyer/dashboard?tab=quotes"))
check("refusal explains the shortfall",
      "only 70 kg" in follow.get_data(as_text=True))
client.get("/logout")
c = con()
check("no order created for the refused acceptance",
      c.execute("SELECT COUNT(*) n FROM bulk_orders WHERE quote_id=?",
                (q4,)).fetchone()["n"] == 0)
check("quote still pending after refusal",
      c.execute("SELECT status FROM bulk_quotes WHERE id=?",
                (q4,)).fetchone()[0] == "Pending")
check("requirement still open after refusal",
      c.execute("SELECT status FROM bulk_requirements WHERE id=?",
                (r4["id"],)).fetchone()[0] == "Open")
check("only 30 kg consumed in total so far",
      round(c.execute("SELECT COALESCE(SUM(qty),0) s FROM supply_consumption "
                      "WHERE bulk_order_id IS NOT NULL").fetchone()["s"]) == 30)
c.close()

# ---------- cancel frees supply so the same pending quote succeeds ----------
client.post("/login", data={"email": "buyerb@test.com", "password": "secret123"})
c = con()
o3 = c.execute("SELECT id FROM bulk_orders WHERE quote_id=?", (q3,)).fetchone()["id"]
c.close()
client.post(f"/bulk-order/{o3}/cancel")
client.get("/logout")
client.post("/login", data={"email": "buyera@test.com", "password": "secret123"})
check("accepting the same 90 kg quote now succeeds", 302 == client.post(
    f"/bulk/quote/{q4}/accept").status_code)
client.get("/logout")
c = con()
check("90 kg order created", c.execute(
    "SELECT status FROM bulk_orders WHERE quote_id=?", (q4,)).fetchone()[0] == "Active")
check("supply on hand now 10 kg", c.execute(
    "SELECT stock FROM products WHERE id=?", (supply["id"],)).fetchone()[0] == 10)
c.close()

# ---------- fpo overview supply stats ----------
client.post("/login", data={"email": "agrifpo@test.com", "password": "secret123"})
body = client.get("/fpo/dashboard?tab=overview").get_data(as_text=True)
check("overview shows aggregate supply stats",
      "Available aggregate supply" in body and "Committed to active bulk orders" in body)
client.get("/logout")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
import sys

sys.exit(1 if fails else 0)