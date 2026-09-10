import app as A

A.init_db()
c = A.app.test_client()
with c.session_transaction() as s:
    s["user_id"] = 3  # customer

# add an item first
conn = __import__("sqlite3").connect(A.DB_PATH)
pid = conn.execute("SELECT id FROM products WHERE stock>0 LIMIT 1").fetchone()[0]
print("product id:", pid)
conn.close()

c.post(f"/cart/add/{pid}", data={"quantity": "2"})
r = c.get("/cart")
print("status:", r.status_code)
h = r.get_data(as_text=True)
print("has table:", "class=\"table\"" in h)
print("--- full body ---")
print(h)