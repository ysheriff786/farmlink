import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    BulkBuyerProfile,
    BulkOrder,
    BulkQuote,
    BulkRequirement,
    FPOProfile,
    LogisticsProfile,
    Message,
    Order,
    OrderItem,
    Product,
    User,
    db,
)
from services.demand_forecast import (
    generate_all_forecasts,
    generate_forecast,
    get_demand_analytics,
    get_farmer_forecasts,
    get_fpo_demand_insight,
)

app = Flask(__name__)
app.config.from_object("config.Config")
basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(basedir, "static", "uploads")
ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMG_SIZE = 5 * 1024 * 1024
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

db.init_app(app)

ROLES = ("farmer", "fpo", "customer", "bulk_buyer", "admin", "logistics")
SELLER_ROLES = ("farmer", "fpo")
BUYER_ROLES = ("customer", "bulk_buyer")
LOGISTICS_ROLES = ("logistics",)
BUYER_TYPES = ["Hotel", "Restaurant", "Retailer", "Supermarket",
               "Food Processor", "Institution", "Other"]
VERIF_STATUSES = ["pending", "verified", "rejected"]
UNITS = ["kg", "gram", "dozen", "piece", "litre", "bunch", "pack"]
CATEGORIES = ["Vegetables", "Fruits", "Flowers", "Grains & Pulses", "Dairy & Eggs", "Honey"]
STATUSES = ["Pending Payment", "Placed", "Confirmed", "Out for Delivery",
            "Delivered", "Cancelled"]

PAGES = {
    "about": ("About Us", """
        <p>FarmLink is an online marketplace that connects local farmers directly with
        customers. Farmers list their fresh produce, and customers order it at fair prices
        with no middlemen.</p>
        <p>Our mission is simple: better income for farmers, fresher food for families.</p>"""),
    "contact": ("Contact Us", """
        <p>For orders, support or feedback, reach us at:</p>
        <p><strong>Email:</strong> support@farmlink.example<br>
        <strong>Phone:</strong> +91 90000 00000 (10 AM - 6 PM)</p>
        <p>We usually reply within one working day.</p>"""),
    "privacy-policy": ("Privacy Policy", """
        <p>We collect only the information needed to run the marketplace: your name,
        email, phone number and delivery address. We never sell your data.</p>
        <p>Passwords are stored securely hashed. You may request deletion of your
        account and data at any time by contacting support.</p>"""),
    "terms": ("Terms of Service", """
        <p>By using FarmLink you agree to provide accurate information and to use the
        platform lawfully. Farmers are responsible for the quality of their produce;
        customers are responsible for timely acceptance of deliveries.</p>
        <p>FarmLink is a marketplace platform and is not the seller of record for
        produce listed by farmers.</p>"""),
    "refund-policy": ("Refund Policy", """
        <p>If an item arrives damaged or spoilt, contact us within 24 hours with a photo.
        Eligible orders receive a full refund to the original payment method within 5-7
        working days. For Cash on Delivery orders, refunds are issued via UPI/bank transfer.</p>"""),
    "shipping-policy": ("Shipping Policy", """
        <p>Orders are delivered directly by farmers or local delivery partners, usually
        within 1-3 days depending on your location. You can track every order status from
        the My Orders page or the Track Order page.</p>"""),
}


def init_db():
    with app.app_context():
        db.create_all()
        inspector = db.inspect(db.engine)
        if "is_verified" not in {c["name"] for c in inspector.get_columns("users")}:
            db.session.execute(text("ALTER TABLE users ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 1"))
            db.session.commit()
        if "username" not in {c["name"] for c in inspector.get_columns("users")}:
            db.session.execute(text("ALTER TABLE users ADD COLUMN username TEXT"))
            db.session.commit()
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def query(sql, params=None, one=False):
    result = db.session.execute(text(sql), params or {})
    if one:
        row = result.mappings().first()
        return dict(row) if row else None
    return [dict(r) for r in result.mappings().fetchall()]


def current_user():
    uid = session.get("user_id")
    if uid:
        return query("SELECT * FROM users WHERE id=:id", {"id": uid}, one=True)
    return None


@app.context_processor
def inject_user():
    user = current_user()
    cart_count = 0
    new_orders = 0
    notification_count = 0
    if user:
        user = dict(user)
        if user["role"] == "fpo":
            user["profile"] = query(
                "SELECT * FROM fpo_profiles WHERE user_id=:uid", {"uid": user["id"]}, one=True)
        elif user["role"] == "bulk_buyer":
            user["profile"] = query(
                "SELECT * FROM bulk_buyer_profiles WHERE user_id=:uid", {"uid": user["id"]}, one=True)
        elif user["role"] == "logistics":
            user["profile"] = query(
                "SELECT * FROM logistics_profiles WHERE user_id=:uid", {"uid": user["id"]}, one=True)
        if user["role"] in BUYER_ROLES:
            cart_count = query("SELECT COUNT(*) AS n FROM cart_items WHERE customer_id=:cid",
                               {"cid": user["id"]}, one=True)["n"]
        elif user["role"] in SELLER_ROLES:
            new_orders = query(
                "SELECT COUNT(DISTINCT o.id) AS n FROM orders o "
                "JOIN order_items oi ON oi.order_id = o.id "
                "WHERE oi.farmer_id=:fid AND o.status='Placed'", {"fid": user["id"]}, one=True)["n"]
        notification_count = query(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id=:uid AND is_read=0",
            {"uid": user["id"]}, one=True)["n"]
    counts = {r["category"]: r["n"] for r in
              query("SELECT category, COUNT(*) AS n FROM products GROUP BY category")}
    categories = [{"name": c, "count": counts.get(c, 0)} for c in CATEGORIES]
    return {"current_user": user, "cart_count": cart_count, "new_orders": new_orders,
            "categories": categories, "notification_count": notification_count}


@app.route("/api/order/<int:oid>/status")
def api_order_status(oid):
    user = current_user()
    if not user:
        return {"error": "login_required"}, 401
    if not _can_access_order(user, oid):
        return {"error": "forbidden"}, 403
    row = query("SELECT id, status, created_at FROM orders WHERE id=:id", {"id": oid}, one=True)
    if not row:
        return {"error": "not_found"}, 404
    return {"id": row["id"], "status": row["status"], "created_at": row["created_at"]}


@app.route("/api/live/counts")
def api_live_counts():
    user = current_user()
    data = {"cart_count": 0, "new_orders": 0, "notification_count": 0}
    if user:
        if user["role"] in BUYER_ROLES:
            data["cart_count"] = query(
                "SELECT COUNT(*) AS n FROM cart_items WHERE customer_id=:cid",
                {"cid": user["id"]}, one=True)["n"]
        elif user["role"] in SELLER_ROLES:
            data["new_orders"] = query(
                "SELECT COUNT(DISTINCT o.id) AS n FROM orders o "
                "JOIN order_items oi ON oi.order_id = o.id "
                "WHERE oi.farmer_id=:fid AND o.status='Placed'", {"fid": user["id"]}, one=True)["n"]
        data["notification_count"] = query(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id=:uid AND is_read=0",
            {"uid": user["id"]}, one=True)["n"]
        data["name"] = user["name"]
    return data


@app.route("/api/stats")
def api_stats():
    return {
        "farmers": query("SELECT COUNT(*) AS n FROM users WHERE role='farmer'", one=True)["n"],
        "products": query("SELECT COUNT(*) AS n FROM products", one=True)["n"],
        "orders": query("SELECT COUNT(*) AS n FROM orders WHERE status!='Cancelled'", one=True)["n"],
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in first.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(role):
    allowed = role if isinstance(role, (tuple, list)) else (role,)

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please log in first.", "error")
                return redirect(url_for("login", next=request.path))
            if user["role"] not in allowed:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.template_filter("money")
def money(value):
    try:
        return f"\u20b9{float(value):,.2f}"
    except (TypeError, ValueError):
        return value


@app.template_filter("datefmt")
def datefmt(value):
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y, %H:%M")
    except ValueError:
        return value


def status_class(status):
    return {
        "Pending Payment": "warn",
        "Placed": "info",
        "Confirmed": "info",
        "Out for Delivery": "progress",
        "Delivered": "done",
        "Cancelled": "bad",
        "Pending": "warn",
        "Accepted": "done",
        "Rejected": "bad",
        "Open": "info",
        "Awarded": "progress",
        "Closed": "done",
        "Active": "progress",
        "Completed": "done",
        "pending": "warn",
        "verified": "done",
        "rejected": "bad",
    }.get(status, "info")


app.jinja_env.filters["status_class"] = status_class


def verif_class(status):
    return {"pending": "warn", "verified": "done", "rejected": "bad"}.get(status, "info")


app.jinja_env.filters["verif_class"] = verif_class


def rzp_available():
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def rzp_create_order(amount_paise, receipt):
    body = json.dumps({"amount": amount_paise, "currency": "INR", "receipt": receipt}).encode()
    auth = f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()
    import base64
    header = base64.b64encode(auth).decode()
    req = urllib.request.Request(
        "https://api.razorpay.com/v1/orders",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {header}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"Razorpay API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Razorpay: {e.reason}")


def rzp_verify_signature(rzp_order_id, payment_id, signature):
    msg = f"{rzp_order_id}|{payment_id}".encode()
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _product_card_query(where="", params=None, order="p.id DESC", limit=""):
    sql = ("SELECT p.*, u.name AS farmer_name, "
           "COALESCE((SELECT AVG(rating) FROM reviews WHERE product_id=p.id), 0) AS avg_rating, "
           "COALESCE((SELECT COUNT(*) FROM reviews WHERE product_id=p.id), 0) AS review_count "
           "FROM products p JOIN users u ON p.farmer_id = u.id " + where)
    if order:
        sql += " ORDER BY " + order
    if limit:
        sql += " LIMIT " + str(limit)
    return query(sql, params or {})


@app.route("/")
def index():
    featured = _product_card_query(limit=8)
    top_products = query(
        "SELECT p.*, u.name AS farmer_name, "
        "COALESCE(sold.qty, 0) AS sold_qty, "
        "COALESCE((SELECT AVG(rating) FROM reviews WHERE product_id=p.id), 0) AS avg_rating, "
        "COALESCE((SELECT COUNT(*) FROM reviews WHERE product_id=p.id), 0) AS review_count "
        "FROM products p JOIN users u ON p.farmer_id = u.id LEFT JOIN ("
        "SELECT oi.product_name, SUM(oi.quantity) AS qty FROM order_items oi GROUP BY oi.product_name"
        ") sold ON sold.product_name = p.name "
        "ORDER BY sold_qty DESC, p.id DESC LIMIT 4")
    return render_template("index.html", featured=featured, top_products=top_products)


@app.route("/product/<int:pid>")
def product_detail(pid):
    product = _product_card_query("WHERE p.id=:pid", {"pid": pid}, limit=1)
    product = product[0] if product else None
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("products"))
    reviews = query(
        "SELECT r.*, u.name AS user_name FROM reviews r JOIN users u ON r.user_id=u.id "
        "WHERE r.product_id=:pid ORDER BY r.id DESC", {"pid": pid})
    my_review = None
    if session.get("user_id"):
        my_review = query("SELECT * FROM reviews WHERE product_id=:pid AND user_id=:uid",
                          {"pid": pid, "uid": session["user_id"]}, one=True)
    related = _product_card_query(
        "WHERE p.category=:cat AND p.id!=:pid", {"cat": product["category"], "pid": pid}, limit=4)
    return render_template("product_detail.html", product=product, reviews=reviews,
                           my_review=my_review, related=related)


@app.route("/product/<int:pid>/review", methods=["POST"])
@login_required
def product_review(pid):
    if not query("SELECT id FROM products WHERE id=:id", {"id": pid}, one=True):
        flash("Product not found.", "error")
        return redirect(url_for("products"))
    try:
        rating = int(request.form.get("rating", 0))
    except ValueError:
        rating = 0
    comment = request.form.get("comment", "").strip()[:500]
    if not 1 <= rating <= 5:
        flash("Please choose a rating from 1 to 5 stars.", "error")
        return redirect(url_for("product_detail", pid=pid))
    existing = query("SELECT id FROM reviews WHERE product_id=:pid AND user_id=:uid",
                     {"pid": pid, "uid": session["user_id"]}, one=True)
    if existing:
        db.session.execute(text("UPDATE reviews SET rating=:rating, comment=:comment WHERE id=:id"),
                           {"rating": rating, "comment": comment, "id": existing["id"]})
        flash("Your review has been updated.", "success")
    else:
        db.session.execute(text("INSERT INTO reviews (product_id, user_id, rating, comment) VALUES (:pid, :uid, :rating, :comment)"),
                           {"pid": pid, "uid": session["user_id"], "rating": rating, "comment": comment})
        flash("Thanks for your review!", "success")
    db.session.commit()
    return redirect(url_for("product_detail", pid=pid))


@app.route("/subscribe", methods=["POST"])
def subscribe():
    email = request.form.get("email", "").strip().lower()
    if "@" not in email or "." not in email or len(email) > 120:
        flash("Please enter a valid email address.", "error")
    else:
        try:
            db.session.execute(text("INSERT INTO subscribers (email) VALUES (:email)"), {"email": email})
            db.session.commit()
            flash("Subscribed! You will receive farming tips and offers.", "success")
        except Exception:
            db.session.rollback()
            flash("You are already subscribed.", "error")
    return redirect(request.referrer or url_for("index"))


@app.route("/track", methods=["GET"])
@login_required
def track():
    oid = request.args.get("order", "").strip()
    result = None
    denied = False
    if oid.isdigit():
        row = query("SELECT o.*, u.name AS buyer_name FROM orders o JOIN users u "
                    "ON o.customer_id=u.id WHERE o.id=:id", {"id": int(oid)}, one=True)
        if row:
            if _can_access_order(current_user(), int(oid)):
                lines = query("SELECT product_name, quantity FROM order_items WHERE order_id=:oid",
                              {"oid": row["id"]})
                result = dict(row, order_lines=[dict(l) for l in lines])
            else:
                denied = True
    return render_template("track.html", oid=oid, result=result, denied=denied)


@app.route("/page/<slug>")
def page(slug):
    if slug not in PAGES:
        flash("Page not found.", "error")
        return redirect(url_for("index"))
    title, body = PAGES[slug]
    return render_template("page.html", title=title, body=body)


def role_home(user):
    return {
        "farmer": url_for("dashboard"),
        "fpo": url_for("fpo_dashboard"),
        "customer": url_for("products"),
        "bulk_buyer": url_for("bulk_dashboard"),
        "admin": url_for("admin"),
        "logistics": url_for("logistics_dashboard"),
    }.get(user["role"], url_for("products"))


def _opt_float(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        phone = "".join(ch for ch in request.form.get("phone", "") if ch.isdigit())[:15]
        if (role == "admin" or role not in ROLES
                or not name or not email or not password):
            flash("Please fill all fields and choose a role.", "error")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("register.html")
        if query("SELECT id FROM users WHERE email=:email", {"email": email}, one=True):
            flash("That email is already registered.", "error")
            return render_template("register.html")

        contact_person = request.form.get("contact_person", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip()
        pincode = "".join(ch for ch in request.form.get("pincode", ""))[:10]
        if role in ("fpo", "bulk_buyer", "logistics"):
            if len(phone) < 10:
                flash("Please enter a valid 10-digit phone number.", "error")
                return render_template("register.html")
            if not all([contact_person, city, state, pincode]):
                flash("Please fill your organisation's contact person, city, state and pincode.",
                      "error")
                return render_template("register.html")
        if role == "fpo":
            registration_id = request.form.get("registration_id", "").strip()
            member_count_raw = request.form.get("member_count", "0").strip()
            try:
                member_count = int(member_count_raw) if member_count_raw else 0
            except ValueError:
                member_count = -1
            if not registration_id:
                flash("FPO registration ID is required.", "error")
                return render_template("register.html")
            if member_count < 0:
                flash("Number of farmer members must be 0 or more.", "error")
                return render_template("register.html")
        elif role == "bulk_buyer":
            buyer_type = request.form.get("buyer_type", "")
            if buyer_type not in BUYER_TYPES:
                flash("Please choose a valid buyer type.", "error")
                return render_template("register.html")
        elif role == "logistics":
            company_name = request.form.get("company_name", "").strip()
            license_number = request.form.get("license_number", "").strip()
            if not company_name:
                flash("Company name is required for logistics partners.", "error")
                return render_template("register.html")

        latitude = _opt_float(request.form.get("latitude"))
        longitude = _opt_float(request.form.get("longitude"))
        city = city or ""
        state = state or ""
        pincode = pincode or ""
        is_verified = role not in ("fpo", "bulk_buyer", "logistics")
        user = User(
            name=name, email=email, password_hash=generate_password_hash(password),
            role=role, phone=phone, address=address, latitude=latitude, longitude=longitude,
            city=city, state=state, pincode=pincode, is_verified=is_verified)
        db.session.add(user)
        db.session.flush()
        uid = user.id
        if role == "fpo":
            db.session.add(FPOProfile(
                user_id=uid, registration_id=registration_id,
                description=request.form.get("description", "").strip(),
                contact_person=contact_person, phone=phone, email=email,
                address=address, city=city, state=state, pincode=pincode,
                latitude=latitude, longitude=longitude, member_count=member_count,
                main_crops=request.form.get("main_crops", "").strip()))
        elif role == "bulk_buyer":
            db.session.add(BulkBuyerProfile(
                user_id=uid, buyer_type=buyer_type, contact_person=contact_person,
                phone=phone, email=email, address=address, city=city, state=state,
                pincode=pincode, latitude=latitude, longitude=longitude))
        elif role == "logistics":
            db.session.add(LogisticsProfile(
                user_id=uid, company_name=company_name, contact_person=contact_person,
                phone=phone, email=email, address=address, city=city, state=state,
                pincode=pincode, latitude=latitude, longitude=longitude,
                license_number=license_number))
        db.session.commit()
        session.clear()
        session["user_id"] = uid
        flash(f"Welcome to FarmLink, {name}!", "success")
        return redirect(role_home({"role": role}))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = query("SELECT * FROM users WHERE email=:email", {"email": email}, one=True)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            flash(f"Welcome back, {user['name']}!", "success")
            nxt = request.form.get("next") or request.args.get("next")
            if nxt and nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(role_home(user))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = "".join(ch for ch in request.form.get("phone", "") if ch.isdigit())[:15]
        address = request.form.get("address", "").strip()
        new_password = request.form.get("new_password", "")
        if not name:
            flash("Name cannot be empty.", "error")
            return render_template("profile.html")
        if new_password:
            if len(new_password) < 6:
                flash("New password must be at least 6 characters.", "error")
                return render_template("profile.html")
            db.session.execute(
                text("UPDATE users SET name=:name, phone=:phone, address=:address, "
                     "password_hash=:pw WHERE id=:id"),
                {"name": name, "phone": phone, "address": address,
                 "pw": generate_password_hash(new_password), "id": user["id"]})
        else:
            db.session.execute(
                text("UPDATE users SET name=:name, phone=:phone, address=:address WHERE id=:id"),
                {"name": name, "phone": phone, "address": address, "id": user["id"]})
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip()
    where = ""
    params = {}
    if q:
        where += " AND (p.name LIKE :q1 OR p.description LIKE :q2)"
        params["q1"] = f"%{q}%"
        params["q2"] = f"%{q}%"
    if cat and cat in CATEGORIES:
        where += " AND p.category=:cat"
        params["cat"] = cat
    items = _product_card_query("WHERE 1=1" + where, params)
    return render_template("products.html", products=items, q=q, cat=cat)


def _save_uploaded_image(old_filename=""):
    file = request.files.get("image")
    if not file or not file.filename:
        return None
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMG_EXT:
        raise ValueError("Image must be .jpg, .png or .webp")
    data = file.read()
    if len(data) > MAX_IMG_SIZE:
        raise ValueError("Image too large (max 5 MB).")
    filename = uuid.uuid4().hex + ext
    with open(os.path.join(UPLOAD_FOLDER, filename), "wb") as out:
        out.write(data)
    if old_filename:
        old_path = os.path.join(UPLOAD_FOLDER, old_filename)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
    return filename


@app.route("/product/new", methods=["GET", "POST"])
@role_required(SELLER_ROLES)
def product_new():
    if request.method == "POST":
        return _save_product(None)
    return render_template("product_form.html", product=None, units=UNITS,
                           categories=CATEGORIES)


@app.route("/product/<int:pid>/edit", methods=["GET", "POST"])
@role_required(SELLER_ROLES)
def product_edit(pid):
    product = query("SELECT * FROM products WHERE id=:id", {"id": pid}, one=True)
    if not product or product["farmer_id"] != session["user_id"]:
        flash("Product not found.", "error")
        return redirect(url_for("products"))
    if request.method == "POST":
        return _save_product(product)
    return render_template("product_form.html", product=product, units=UNITS,
                           categories=CATEGORIES)


def _save_product(product):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    unit = request.form.get("unit", "kg")
    category = request.form.get("category", "Vegetables")
    if category not in CATEGORIES:
        category = "Vegetables"
    try:
        price = float(request.form.get("price", 0))
        stock = int(request.form.get("stock", 0))
        mrp_raw = (request.form.get("mrp") or "").strip()
        mrp = float(mrp_raw) if mrp_raw else None
    except ValueError:
        flash("Price, MRP and stock must be numbers.", "error")
        return render_template("product_form.html", product=product, units=UNITS,
                               categories=CATEGORIES)
    if unit not in UNITS:
        unit = "kg"
    if mrp is not None and mrp < price:
        mrp = None
    if not name or price <= 0 or stock < 0:
        flash("Enter a name, a price above 0 and stock of 0 or more.", "error")
        return render_template("product_form.html", product=product, units=UNITS,
                               categories=CATEGORIES)

    image = product["image"] if product else ""
    try:
        uploaded = _save_uploaded_image(image)
        if uploaded:
            image = uploaded
    except ValueError as e:
        flash(str(e), "error")
        return render_template("product_form.html", product=product, units=UNITS,
                               categories=CATEGORIES)

    if product is None:
        p = Product(farmer_id=session["user_id"], name=name, description=description,
                    price=price, mrp=mrp, category=category, unit=unit, stock=stock, image=image)
        db.session.add(p)
        db.session.flush()
        pid = p.id
    else:
        db.session.execute(
            text("UPDATE products SET name=:name, description=:desc, price=:price, mrp=:mrp, "
                 "category=:cat, unit=:unit, stock=:stock, image=:img WHERE id=:id"),
            {"name": name, "desc": description, "price": price, "mrp": mrp, "cat": category,
             "unit": unit, "stock": stock, "img": image, "id": product["id"]})
        pid = product["id"]
    db.session.commit()
    if not (product and product["is_aggregate"]):
        if _refresh_aggregates_for(pid):
            db.session.commit()
    flash("Product saved.", "success")
    return redirect(url_for("products"))


@app.route("/product/<int:pid>/delete", methods=["POST"])
@role_required(SELLER_ROLES)
def product_delete(pid):
    product = query("SELECT * FROM products WHERE id=:id", {"id": pid}, one=True)
    if product and product["farmer_id"] == session["user_id"]:
        _delete_product(product)
        flash("Product deleted.", "success")
    else:
        flash("Product not found.", "error")
    return redirect(url_for("products"))


def _approved_member_farmer_ids(fpo_id):
    rows = query(
        "SELECT farmer_id FROM fpo_members WHERE fpo_id=:fid AND status='approved'",
        {"fid": fpo_id})
    return {r["farmer_id"] for r in rows}


def _member_products(fpo_id):
    ids = _approved_member_farmer_ids(fpo_id)
    if not ids:
        return []
    placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": i for i, i in enumerate(ids)}
    return query(
        f"SELECT * FROM products WHERE is_aggregate=0 AND farmer_id IN ({placeholders}) "
        "ORDER BY name, id", params)


def _committed_total(product_id):
    row = query(
        "SELECT COALESCE(SUM(committed_qty), 0) AS s FROM supply_allocations WHERE product_id=:pid",
        {"pid": product_id}, one=True)
    return row["s"]


def _aggregate_on_hand(supply_id):
    rows = query(
        "SELECT a.committed_qty AS committed, p.stock AS stock FROM supply_allocations a "
        "JOIN products p ON p.id = a.product_id WHERE a.supply_id=:sid ORDER BY a.id",
        {"sid": supply_id})
    return round(sum(min(r["committed"], r["stock"]) for r in rows), 2)


def _aggregate_sources(supply_id):
    return query(
        "SELECT a.id AS allocation_id, a.committed_qty AS committed, u.name AS farmer_name, "
        "p.id AS product_id, p.name AS product_name, p.unit, p.stock AS available "
        "FROM supply_allocations a JOIN products p ON p.id = a.product_id "
        "JOIN users u ON u.id = p.farmer_id WHERE a.supply_id=:sid ORDER BY a.id",
        {"sid": supply_id})


def _aggregate_consumption(supply_id):
    return query(
        "SELECT sc.id, sc.qty, sc.created_at, p.name AS product_name, u.name AS farmer_name, "
        "COALESCE((SELECT o.id FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        "          WHERE oi.id = sc.order_item_id), sc.bulk_order_id) AS order_id, "
        "CASE WHEN sc.order_item_id IS NOT NULL THEN 'retail' ELSE 'bulk' END AS kind "
        "FROM supply_consumption sc "
        "JOIN supply_allocations a ON a.id = sc.allocation_id "
        "JOIN products p ON p.id = a.product_id JOIN users u ON u.id = p.farmer_id "
        "WHERE sc.supply_id=:sid ORDER BY sc.id DESC LIMIT 100", {"sid": supply_id})


def _refresh_aggregate(supply_id):
    on_hand = _aggregate_on_hand(supply_id)
    db.session.execute(
        text("UPDATE products SET stock=:stock WHERE id=:id AND is_aggregate=1"),
        {"stock": on_hand, "id": supply_id})


def _refresh_aggregates_for(product_id):
    rows = query(
        "SELECT DISTINCT supply_id FROM supply_allocations WHERE product_id=:pid",
        {"pid": product_id})
    for r in rows:
        _refresh_aggregate(r["supply_id"])
    return bool(rows)


def _consume_aggregate(product, qty, order_item_id=None, bulk_order_id=None):
    supply_id = product["id"]
    remaining = float(qty)
    rows = query(
        "SELECT a.id AS allocation_id, a.committed_qty AS committed, p.id AS product_id, p.stock AS stock "
        "FROM supply_allocations a JOIN products p ON p.id = a.product_id "
        "WHERE a.supply_id=:sid ORDER BY a.id", {"sid": supply_id})
    for r in rows:
        if remaining <= 0:
            break
        avail = min(r["committed"], r["stock"])
        take = min(avail, remaining)
        if take <= 0:
            continue
        db.session.execute(text("UPDATE products SET stock = stock - :take WHERE id=:id"),
                           {"take": take, "id": r["product_id"]})
        db.session.execute(text("UPDATE supply_allocations SET committed_qty = committed_qty - :take WHERE id=:id"),
                           {"take": take, "id": r["allocation_id"]})
        db.session.execute(
            text("INSERT INTO supply_consumption (supply_id, order_item_id, allocation_id, "
                 "bulk_order_id, qty) VALUES (:sid, :oiid, :aid, :boid, :qty)"),
            {"sid": supply_id, "oiid": order_item_id, "aid": r["allocation_id"],
             "boid": bulk_order_id, "qty": take})
        remaining -= take
    taken = float(qty) - remaining
    db.session.execute(text("UPDATE products SET stock = stock - :taken WHERE id=:id"),
                       {"taken": taken, "id": supply_id})
    return taken


def _consume_aggregate_line(product, qty, order_item_id):
    return _consume_aggregate(product, qty, order_item_id=order_item_id)


def _consume_aggregate_bulk(product, qty, bulk_order_id):
    return _consume_aggregate(product, qty, bulk_order_id=bulk_order_id)


def _restore_consumption_rows(rows):
    if not rows:
        return False
    for a_id in sorted({r["allocation_id"] for r in rows}):
        qty = sum(r["qty"] for r in rows if r["allocation_id"] == a_id)
        db.session.execute(text("UPDATE supply_allocations SET committed_qty = committed_qty + :qty WHERE id=:id"),
                           {"qty": qty, "id": a_id})
        db.session.execute(text("UPDATE products SET stock = stock + :qty WHERE id = "
                           "(SELECT product_id FROM supply_allocations WHERE id=:aid)"),
                           {"qty": qty, "aid": a_id})
    for s_id in sorted({r["supply_id"] for r in rows}):
        qty = sum(r["qty"] for r in rows if r["supply_id"] == s_id)
        db.session.execute(text("UPDATE products SET stock = stock + :qty WHERE id=:id"),
                           {"qty": qty, "id": s_id})
    return True


def _restore_aggregate_line(order_item_id):
    rows = query(
        "SELECT sc.supply_id, sc.allocation_id, sc.qty FROM supply_consumption sc "
        "WHERE sc.order_item_id=:oiid ORDER BY sc.id", {"oiid": order_item_id})
    if not rows:
        return False
    _restore_consumption_rows(rows)
    for s_id in sorted({r["supply_id"] for r in rows}):
        db.session.execute(text("DELETE FROM supply_consumption WHERE supply_id=:sid AND order_item_id=:oiid"),
                           {"sid": s_id, "oiid": order_item_id})
    return True


def _restore_aggregate_bulk(bulk_order_id):
    rows = query(
        "SELECT sc.supply_id, sc.allocation_id, sc.qty FROM supply_consumption sc "
        "WHERE sc.bulk_order_id=:boid ORDER BY sc.id", {"boid": bulk_order_id})
    if not rows:
        return False
    _restore_consumption_rows(rows)
    for s_id in sorted({r["supply_id"] for r in rows}):
        db.session.execute(text("DELETE FROM supply_consumption WHERE supply_id=:sid AND bulk_order_id=:boid"),
                           {"sid": s_id, "boid": bulk_order_id})
    return True


def _fpo_supply(fpo_id, sid):
    return query(
        "SELECT * FROM products WHERE id=:id AND farmer_id=:fid AND is_aggregate=1",
        {"id": sid, "fid": fpo_id}, one=True)


def _fpo_aggregate_summary(fpo_id):
    return query(
        "SELECT p.id, p.name, p.price, p.unit, p.min_qty, "
        "COALESCE((SELECT SUM(MIN(a.committed_qty, pp.stock)) FROM supply_allocations a "
        "JOIN products pp ON pp.id = a.product_id WHERE a.supply_id = p.id), 0) AS on_hand, "
        "(SELECT COUNT(*) FROM supply_allocations a WHERE a.supply_id = p.id) AS sources "
        "FROM products p WHERE p.farmer_id=:fid AND p.is_aggregate=1 ORDER BY p.name",
        {"fid": fpo_id})


_MATCH_STOPWORDS = {
    "kg", "litre", "dozen", "pack", "bunch", "weekly", "daily", "monthly", "fresh",
    "supply", "supplies", "pool", "fpo", "farm", "farms", "bulk", "new", "crop",
    "needed", "need", "want", "require", "requires", "for", "of", "the", "and",
    "classic", "premium", "quality", "request", "regular", "quantity",
    "kitchen", "hotel", "restaurant", "order", "orders",
}


def _match_tokens(txt):
    tokens = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9]*", txt.lower()):
        if len(raw) >= 3 and raw not in _MATCH_STOPWORDS:
            tokens.add(raw)
    return tokens


def _match_supply(req, supply):
    if (req["unit"] != supply["unit"] or req["category"] != supply["category"]):
        return False
    return bool(_match_tokens(req["title"]) & _match_tokens(supply["name"]))


def _matching_supplies(fpo_id, req):
    rows = query(
        "SELECT * FROM products WHERE farmer_id=:fid AND is_aggregate=1", {"fid": fpo_id})
    matches = []
    for s in rows:
        if _match_supply(req, s):
            on_hand = _aggregate_on_hand(s["id"])
            matches.append({"id": s["id"], "name": s["name"], "unit": s["unit"],
                            "price": s["price"], "on_hand": on_hand})
    return matches


def _bulk_consumption(bulk_order_id):
    return query(
        "SELECT sc.qty, u.name AS farmer_name, p.name AS product_name "
        "FROM supply_consumption sc "
        "JOIN supply_allocations a ON a.id = sc.allocation_id "
        "JOIN products p ON p.id = a.product_id "
        "JOIN users u ON u.id = p.farmer_id "
        "WHERE sc.bulk_order_id=:boid ORDER BY sc.id", {"boid": bulk_order_id})


def _fpo_supply_stats(fpo_id):
    available = sum(m["on_hand"] for m in _fpo_aggregate_summary(fpo_id))
    committed = query(
        "SELECT COALESCE(SUM(sc.qty), 0) AS s FROM supply_consumption sc "
        "JOIN bulk_orders bo ON bo.id = sc.bulk_order_id "
        "WHERE bo.fpo_id=:fid AND bo.status='Active'", {"fid": fpo_id}, one=True)["s"]
    pending = query("SELECT COUNT(*) AS n FROM bulk_quotes WHERE fpo_id=:fid AND status='Pending'",
                    {"fid": fpo_id}, one=True)["n"]
    active = query("SELECT COUNT(*) AS n FROM bulk_orders WHERE fpo_id=:fid AND status='Active'",
                   {"fid": fpo_id}, one=True)["n"]
    return {"available": available, "committed": committed,
            "pending": pending, "active": active}


def _delete_product(product):
    pid = product["id"]
    affected = []
    if product["is_aggregate"]:
        db.session.execute(text("DELETE FROM supply_consumption WHERE supply_id=:pid"), {"pid": pid})
        db.session.execute(text("DELETE FROM supply_allocations WHERE supply_id=:pid"), {"pid": pid})
    else:
        affected = [r["supply_id"] for r in query(
            "SELECT DISTINCT supply_id FROM supply_allocations WHERE product_id=:pid",
            {"pid": pid})]
        db.session.execute(text("DELETE FROM supply_consumption WHERE allocation_id IN "
                           "(SELECT id FROM supply_allocations WHERE product_id=:pid)"), {"pid": pid})
        db.session.execute(text("DELETE FROM supply_allocations WHERE product_id=:pid"), {"pid": pid})
    db.session.execute(text("DELETE FROM cart_items WHERE product_id=:pid"), {"pid": pid})
    db.session.execute(text("DELETE FROM products WHERE id=:pid"), {"pid": pid})
    db.session.commit()
    for sid in affected:
        _refresh_aggregate(sid)
    if affected:
        db.session.commit()
    if product["image"]:
        path = os.path.join(UPLOAD_FOLDER, product["image"])
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _cart_rows(customer_id):
    return query(
        "SELECT ci.id AS item_id, ci.quantity, p.id AS pid, p.name, p.price, p.unit, p.stock, "
        "u.name AS farmer_name "
        "FROM cart_items ci JOIN products p ON ci.product_id = p.id "
        "JOIN users u ON p.farmer_id = u.id WHERE ci.customer_id=:cid ORDER BY ci.id",
        {"cid": customer_id})


@app.route("/cart")
@role_required(BUYER_ROLES)
def cart():
    items = _cart_rows(session["user_id"])
    total = sum(i["price"] * i["quantity"] for i in items)
    return render_template("cart.html", items=items, total=total)


@app.route("/cart/add/<int:pid>", methods=["POST"])
@role_required(BUYER_ROLES)
def cart_add(pid):
    product = query("SELECT * FROM products WHERE id=:id", {"id": pid}, one=True)
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("products"))
    try:
        qty = int(request.form.get("quantity", 1))
    except ValueError:
        qty = 1
    qty = max(1, qty)
    available = (_aggregate_on_hand(pid) if product["is_aggregate"]
                 else product["stock"])
    min_qty = product["min_qty"] or 1
    if qty < min_qty:
        flash(f"Minimum order for {product['name']} is {int(min_qty)} {product['unit']}.", "error")
        return redirect(url_for("product_detail", pid=pid))
    existing = query(
        "SELECT * FROM cart_items WHERE customer_id=:cid AND product_id=:pid",
        {"cid": session["user_id"], "pid": pid}, one=True)
    already = existing["quantity"] if existing else 0
    if available <= 0 or already + qty > available:
        left = max(available - already, 0)
        flash(f"Only {left} left in stock for {product['name']}.", "error")
        return redirect(url_for("products"))
    if existing:
        db.session.execute(text("UPDATE cart_items SET quantity=:qty WHERE id=:id"),
                           {"qty": existing["quantity"] + qty, "id": existing["id"]})
    else:
        db.session.execute(text("INSERT INTO cart_items (customer_id, product_id, quantity) VALUES (:cid, :pid, :qty)"),
                           {"cid": session["user_id"], "pid": pid, "qty": qty})
    db.session.commit()
    flash(f"{product['name']} added to cart.", "success")
    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:item_id>", methods=["POST"])
@role_required(BUYER_ROLES)
def cart_remove(item_id):
    item = query("SELECT * FROM cart_items WHERE id=:id", {"id": item_id}, one=True)
    if item and item["customer_id"] == session["user_id"]:
        db.session.execute(text("DELETE FROM cart_items WHERE id=:id"), {"id": item_id})
        db.session.commit()
        flash("Item removed from cart.", "success")
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET"])
@role_required(BUYER_ROLES)
def checkout():
    items = _cart_rows(session["user_id"])
    if not items:
        flash("Your cart is empty.", "error")
        return redirect(url_for("products"))
    user = current_user()
    total = sum(i["price"] * i["quantity"] for i in items)
    return render_template("checkout.html", items=items, total=total, user=user,
                           online_enabled=rzp_available())


@app.route("/place-order", methods=["POST"])
@role_required(BUYER_ROLES)
def place_order():
    items = _cart_rows(session["user_id"])
    if not items:
        flash("Your cart is empty.", "error")
        return redirect(url_for("products"))
    user = current_user()
    phone = "".join(ch for ch in request.form.get("phone", "") if ch.isdigit())[:15]
    address = request.form.get("address", "").strip()
    payment_method = request.form.get("payment_method", "COD")
    if len(phone) < 10:
        flash("Please enter a valid 10-digit phone number.", "error")
        return redirect(url_for("checkout"))
    if len(address) < 10:
        flash("Please enter a complete delivery address.", "error")
        return redirect(url_for("checkout"))
    if payment_method not in ("COD", "Online"):
        payment_method = "COD"
    if payment_method == "Online" and not rzp_available():
        flash("Online payment is not configured yet. Please choose Cash on Delivery.",
              "error")
        return redirect(url_for("checkout"))
    for i in items:
        p = query("SELECT * FROM products WHERE id=:id", {"id": i["pid"]}, one=True)
        if p and p["is_aggregate"]:
            on_hand = _aggregate_on_hand(i["pid"])
            if i["quantity"] > on_hand:
                flash(f"Only {int(on_hand)} left of {i['name']}. Please update your cart.",
                      "error")
                return redirect(url_for("cart"))
        elif i["quantity"] > i["stock"]:
            flash(f"Not enough stock for {i['name']}. Please update your cart.", "error")
            return redirect(url_for("cart"))

    total = sum(i["price"] * i["quantity"] for i in items)
    status = "Placed" if payment_method == "COD" else "Pending Payment"
    order = Order(customer_id=user["id"], total=total, status=status,
                  payment_method=payment_method, address=address, phone=phone)
    db.session.add(order)
    db.session.flush()
    order_id = order.id
    for i in items:
        oi = OrderItem(order_id=order_id, product_name=i["name"],
                       farmer_id=_farmer_of(i["pid"]), quantity=i["quantity"], price=i["price"])
        db.session.add(oi)
        db.session.flush()
        oi_id = oi.id
        p = query("SELECT * FROM products WHERE id=:id", {"id": i["pid"]}, one=True)
        if p and p["is_aggregate"]:
            _consume_aggregate_line(p, i["quantity"], oi_id)
        else:
            db.session.execute(text("UPDATE products SET stock = stock - :qty WHERE id=:id"),
                               {"qty": i["quantity"], "id": i["pid"]})
        db.session.execute(text("DELETE FROM cart_items WHERE id=:id"), {"id": i["item_id"]})
    db.session.execute(text("UPDATE users SET phone=:phone, address=:addr WHERE id=:id"),
                       {"phone": phone, "addr": address, "id": user["id"]})
    db.session.commit()

    from services.notifications import notify_farmer_new_order
    for fid in {_farmer_of(i["pid"]) for i in items}:
        notify_farmer_new_order(db.session, fid, order_id, total)

    if payment_method == "Online":
        try:
            rzp_order = rzp_create_order(round(total * 100), f"order_{order_id}")
            db.session.execute(text("UPDATE orders SET rzp_order_id=:rzp_id WHERE id=:id"),
                               {"rzp_id": rzp_order["id"], "id": order_id})
            db.session.commit()
        except RuntimeError as e:
            db.session.execute(text("UPDATE orders SET status='Cancelled' WHERE id=:id"), {"id": order_id})
            db.session.commit()
            app.logger.error(str(e))
            flash("Could not start online payment. Please try again or use Cash on Delivery.",
                  "error")
            return redirect(url_for("cart"))
        return redirect(url_for("pay", oid=order_id))

    flash(f"Order #{order_id} placed! Pay cash on delivery.", "success")
    return redirect(url_for("orders"))


def _farmer_of(pid):
    row = query("SELECT farmer_id FROM products WHERE id=:id", {"id": pid}, one=True)
    return row["farmer_id"] if row else 0


@app.route("/pay/<int:oid>")
@role_required(BUYER_ROLES)
def pay(oid):
    order = query("SELECT * FROM orders WHERE id=:id", {"id": oid}, one=True)
    if not order or order["customer_id"] != session["user_id"]:
        flash("Order not found.", "error")
        return redirect(url_for("orders"))
    if order["status"] != "Pending Payment":
        return redirect(url_for("orders"))
    rzp_order_id = order["rzp_order_id"]
    if not rzp_order_id:
        try:
            rzp_order = rzp_create_order(round(order["total"] * 100), f"order_{oid}")
            rzp_order_id = rzp_order["id"]
            db.session.execute(text("UPDATE orders SET rzp_order_id=:rzp_id WHERE id=:id"),
                               {"rzp_id": rzp_order_id, "id": oid})
            db.session.commit()
        except RuntimeError as e:
            app.logger.error(str(e))
            flash("Payment gateway error. Please try again later.", "error")
            return redirect(url_for("orders"))
    return render_template("pay.html", order=order, key_id=RAZORPAY_KEY_ID,
                           rzp_order_id=rzp_order_id)


@app.route("/pay/<int:oid>/verify", methods=["POST"])
@role_required(BUYER_ROLES)
def pay_verify(oid):
    order = query("SELECT * FROM orders WHERE id=:id", {"id": oid}, one=True)
    if not order or order["customer_id"] != session["user_id"]:
        flash("Order not found.", "error")
        return redirect(url_for("orders"))
    if order["status"] != "Pending Payment":
        return redirect(url_for("orders"))
    payment_id = request.form.get("razorpay_payment_id", "")
    rzp_order_id = request.form.get("razorpay_order_id", "")
    signature = request.form.get("razorpay_signature", "")
    if (rzp_order_id == order["rzp_order_id"] and payment_id
            and rzp_verify_signature(rzp_order_id, payment_id, signature)):
        db.session.execute(text("UPDATE orders SET status='Placed', payment_id=:pid WHERE id=:id"),
                           {"pid": payment_id, "id": oid})
        db.session.commit()
        flash(f"Payment received! Order #{oid} confirmed.", "success")
    else:
        flash("We could not verify your payment. If money was deducted it will be refunded "
              "by the bank automatically. You can retry payment from My Orders.", "error")
    return redirect(url_for("orders"))


def _can_change(role, current, target):
    farmer_flow = {
        "Placed": {"Confirmed", "Cancelled"},
        "Confirmed": {"Out for Delivery", "Cancelled"},
        "Out for Delivery": {"Delivered"},
    }
    admin_flow = dict(farmer_flow)
    admin_flow["Confirmed"] = {"Out for Delivery", "Cancelled", "Placed"}
    table = farmer_flow if role in SELLER_ROLES else admin_flow if role == "admin" else {}
    allowed = table.get(current, set())
    if role in BUYER_ROLES:
        allowed = {"Cancelled"} if current in ("Pending Payment", "Placed", "Confirmed") else set()
    return target in allowed


@app.route("/orders")
@login_required
def orders():
    user = current_user()
    if user["role"] in SELLER_ROLES:
        groups = _farmer_order_groups(user["id"])
        has_products = bool(query(
            "SELECT id FROM products WHERE farmer_id=:fid LIMIT 1", {"fid": user["id"]}))
        return render_template("orders.html", sold_groups=groups, has_products=has_products)
    rows = query("SELECT * FROM orders WHERE customer_id=:cid ORDER BY id DESC", {"cid": user["id"]})
    my_orders = []
    for o in rows:
        lines = query("SELECT * FROM order_items WHERE order_id=:oid", {"oid": o["id"]})
        my_orders.append(dict(o, order_lines=lines))
    return render_template("orders.html", orders=my_orders)


def _farmer_order_groups(farmer_id):
    rows = query(
        "SELECT oi.product_name, oi.quantity, oi.price, oi.order_id, "
        "o.status, o.created_at AS order_date, o.total AS order_total, "
        "o.payment_method, o.address, o.phone, u.name AS buyer_name "
        "FROM order_items oi JOIN orders o ON oi.order_id = o.id "
        "JOIN users u ON o.customer_id = u.id "
        "WHERE oi.farmer_id=:fid ORDER BY oi.id DESC", {"fid": farmer_id})
    groups = {}
    for r in rows:
        gid = r["order_id"]
        if gid not in groups:
            groups[gid] = {
                "order_id": gid, "buyer_name": r["buyer_name"],
                "phone": r["phone"], "address": r["address"],
                "order_date": r["order_date"], "status": r["status"],
                "payment_method": r["payment_method"], "total": r["order_total"],
                "lines": []}
        groups[gid]["lines"].append(
            {"product_name": r["product_name"], "quantity": r["quantity"], "price": r["price"]})
    return sorted(groups.values(), key=lambda x: x["order_id"], reverse=True)


@app.route("/order/<int:oid>/status", methods=["POST"])
@login_required
def order_status(oid):
    user = current_user()
    order = query("SELECT * FROM orders WHERE id=:id", {"id": oid}, one=True)
    if not order:
        flash("Order not found.", "error")
        return redirect(url_for("orders"))
    target = request.form.get("status", "")
    owns_product = bool(query(
        "SELECT id FROM order_items WHERE order_id=:oid AND farmer_id=:fid LIMIT 1",
        {"oid": oid, "fid": user["id"]}, one=True))
    if user["role"] in SELLER_ROLES and not owns_product:
        flash("Not your order.", "error")
        return redirect(url_for("orders"))
    if user["role"] in BUYER_ROLES and order["customer_id"] != user["id"]:
        flash("Not your order.", "error")
        return redirect(url_for("orders"))
    if not _can_change(user["role"], order["status"], target):
        flash("That status change is not allowed.", "error")
        return redirect(url_for("orders"))
    if target == "Out for Delivery" and not order["delivery_otp"]:
        otp = f"{secrets.randbelow(1000000):06d}"
        db.session.execute(text("UPDATE orders SET delivery_otp=:otp WHERE id=:id"),
                           {"otp": otp, "id": oid})
    if target == "Delivered":
        supplied = request.form.get("otp", "").strip()
        if user["role"] == "admin":
            pass
        elif not order["delivery_otp"] or supplied != order["delivery_otp"]:
            flash("Wrong or missing delivery OTP. Ask the customer for the 6-digit code.",
                  "error")
            return redirect(url_for("orders"))
    if target == "Cancelled":
        lines = query("SELECT * FROM order_items WHERE order_id=:oid", {"oid": oid})
        for line in lines:
            if _restore_aggregate_line(line["id"]):
                continue
            db.session.execute(
                text("UPDATE products SET stock = stock + :qty WHERE name=:name AND farmer_id=:fid"),
                {"qty": line["quantity"], "name": line["product_name"], "fid": line["farmer_id"]})
    db.session.execute(text("UPDATE orders SET status=:status WHERE id=:id"),
                       {"status": target, "id": oid})
    db.session.commit()
    from services.notifications import notify_order_status
    farmer_ids = [r["farmer_id"] for r in query(
        "SELECT DISTINCT farmer_id FROM order_items WHERE order_id=:oid", {"oid": oid})]
    notify_order_status(db.session, oid, target, order["customer_id"], farmer_ids)
    if target == "Placed":
        from services.notifications import notify_farmer_new_order
        for fid in farmer_ids:
            notify_farmer_new_order(db.session, fid, oid, order["total"])
    flash(f"Order #{oid} updated to '{target}'.", "success")
    return redirect(url_for("orders"))


def _can_access_order(user, oid):
    if not user:
        return False
    if user["role"] == "admin":
        return True
    if user["role"] in BUYER_ROLES:
        return bool(query("SELECT id FROM orders WHERE id=:oid AND customer_id=:cid",
                          {"oid": oid, "cid": user["id"]}, one=True))
    return bool(query("SELECT id FROM order_items WHERE order_id=:oid AND farmer_id=:fid LIMIT 1",
                      {"oid": oid, "fid": user["id"]}, one=True))


@app.route("/api/order/<int:oid>/messages")
@login_required
def api_messages(oid):
    if not _can_access_order(current_user(), oid):
        return {"error": "forbidden"}, 403
    rows = query(
        "SELECT m.id, m.sender_id, m.text, m.created_at, u.name AS sender_name, u.role "
        "FROM messages m JOIN users u ON m.sender_id=u.id "
        "WHERE m.order_id=:oid ORDER BY m.id", {"oid": oid})
    uid = session["user_id"]
    return {"order_id": oid, "messages": [
        dict(r, mine=r["sender_id"] == uid) for r in rows]}


@app.route("/api/order/<int:oid>/messages", methods=["POST"])
@login_required
def api_message_send(oid):
    user = current_user()
    if not _can_access_order(user, oid):
        return {"error": "forbidden"}, 403
    text_content = (request.get_json(silent=True) or {}).get("text", "").strip()[:500]
    if not text_content:
        return {"error": "empty"}, 400
    msg = Message(order_id=oid, sender_id=user["id"], text=text_content)
    db.session.add(msg)
    db.session.flush()
    db.session.commit()
    return {"ok": True, "id": msg.id, "created_at":
            query("SELECT created_at FROM messages WHERE id=:id", {"id": msg.id}, one=True)["created_at"]}


FARMER_EARNING_STATUSES = ("Confirmed", "Out for Delivery", "Delivered")


@app.route("/dashboard")
@role_required("farmer")
def dashboard():
    fid = session["user_id"]
    has_products = bool(query(
        "SELECT id FROM products WHERE farmer_id=:fid LIMIT 1", {"fid": fid}))
    earn_where = f"o.status IN ({','.join(f':es{i}' for i in range(len(FARMER_EARNING_STATUSES)))})"
    base_params = {f"es{i}": s for i, s in enumerate(FARMER_EARNING_STATUSES)}
    base_params["fid"] = fid

    totals = query(
        f"SELECT COUNT(DISTINCT o.id) AS orders, COALESCE(SUM(oi.quantity*oi.price),0) AS revenue "
        f"FROM order_items oi JOIN orders o ON oi.order_id=o.id "
        f"WHERE oi.farmer_id=:fid AND {earn_where}", base_params, one=True)
    month_params = dict(base_params)
    month_rev = query(
        f"SELECT COALESCE(SUM(oi.quantity*oi.price),0) AS s FROM order_items oi "
        f"JOIN orders o ON oi.order_id=o.id "
        f"WHERE oi.farmer_id=:fid AND strftime('%Y-%m', o.created_at)=strftime('%Y-%m','now') "
        f"AND {earn_where}", month_params, one=True)["s"]

    days = []
    day_params = dict(base_params)
    by_day = {r["d"]: r["amt"] for r in query(
        f"SELECT date(o.created_at) AS d, SUM(oi.quantity*oi.price) AS amt "
        f"FROM order_items oi JOIN orders o ON oi.order_id=o.id "
        f"WHERE oi.farmer_id=:fid AND {earn_where} GROUP BY date(o.created_at)",
        day_params)}
    today = date.today()
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        days.append({"label": d.strftime("%d %b"), "amount": round(by_day.get(key, 0), 2)})
    max_amt = max((d["amount"] for d in days), default=0)

    top_products = query(
        "SELECT oi.product_name, SUM(oi.quantity) AS qty, SUM(oi.quantity*oi.price) AS rev "
        "FROM order_items oi JOIN orders o ON oi.order_id=o.id "
        "WHERE oi.farmer_id=:fid AND o.status!='Cancelled' "
        "GROUP BY oi.product_name ORDER BY rev DESC LIMIT 5", {"fid": fid})
    recent_orders = _farmer_order_groups(fid)[:5]
    avg_order = round(totals["revenue"] / totals["orders"], 2) if totals["orders"] else 0

    farmer_forecasts = get_farmer_forecasts(fid)

    today = date.today()
    from services.pricing import PLATFORM_FEE_PERCENT
    items_realized = query(
        f"SELECT oi.product_name, SUM(oi.quantity) AS qty, SUM(oi.quantity*oi.price) AS gross, "
        f"AVG(oi.price) AS avg_price FROM order_items oi "
        f"JOIN orders o ON oi.order_id=o.id "
        f"WHERE oi.farmer_id=:fid AND {earn_where} "
        f"AND o.created_at >= datetime('now','-60 days') "
        f"GROUP BY oi.product_name ORDER BY gross DESC LIMIT 6", dict(base_params))
    price_transparency = []
    for row in items_realized or []:
        fee = round(row["gross"] * PLATFORM_FEE_PERCENT / 100, 2)
        price_transparency.append({
            "product": row["product_name"],
            "qty": row["qty"],
            "avg_price": round(row["avg_price"], 2),
            "gross": round(row["gross"], 2),
            "fee": fee,
            "realized": round(row["gross"] - fee, 2),
        })
    price_transparency_total = {
        "gross": round(sum(t["gross"] for t in price_transparency), 2),
        "fee": round(sum(t["fee"] for t in price_transparency), 2),
        "realized": round(sum(t["realized"] for t in price_transparency), 2),
    }

    return render_template(
        "dashboard.html", totals=totals, month_rev=month_rev, avg_order=avg_order,
        days=days, max_amt=max_amt, top_products=top_products,
        recent_orders=recent_orders, farmer_forecasts=farmer_forecasts,
        price_transparency=price_transparency, price_transparency_total=price_transparency_total,
        has_products=has_products)


@app.route("/admin")
@role_required("admin")
def admin():
    stats = {
        "users": query("SELECT COUNT(*) AS n FROM users", one=True)["n"],
        "farmers": query("SELECT COUNT(*) AS n FROM users WHERE role='farmer'", one=True)["n"],
        "customers": query("SELECT COUNT(*) AS n FROM users WHERE role='customer'", one=True)["n"],
        "fpos": query("SELECT COUNT(*) AS n FROM users WHERE role='fpo'", one=True)["n"],
        "bulk_buyers": query("SELECT COUNT(*) AS n FROM users WHERE role='bulk_buyer'",
                             one=True)["n"],
        "pending_verif": query(
            "SELECT (SELECT COUNT(*) FROM fpo_profiles WHERE verification_status='pending') + "
            "(SELECT COUNT(*) FROM bulk_buyer_profiles WHERE verification_status='pending') AS n",
            one=True)["n"],
        "products": query("SELECT COUNT(*) AS n FROM products", one=True)["n"],
        "orders": query("SELECT COUNT(*) AS n FROM orders WHERE status!='Cancelled'", one=True)["n"],
        "revenue": query(
            "SELECT COALESCE(SUM(total),0) AS s FROM orders "
            "WHERE status='Delivered' AND payment_method='Online'", one=True)["s"]
        + query(
            "SELECT COALESCE(SUM(total),0) AS s FROM orders "
            "WHERE status IN ('Delivered','Out for Delivery','Confirmed') "
            "AND payment_method='COD'", one=True)["s"],
    }
    recent = query(
        "SELECT o.*, u.name AS buyer_name FROM orders o "
        "JOIN users u ON o.customer_id = u.id ORDER BY o.id DESC LIMIT 25")
    prods = query(
        "SELECT p.*, u.name AS farmer_name FROM products p "
        "JOIN users u ON p.farmer_id = u.id ORDER BY p.id DESC")
    users = query("SELECT * FROM users ORDER BY id DESC")
    fpos = query(
        "SELECT u.*, fp.registration_id, fp.member_count, fp.city, fp.verification_status "
        "FROM users u JOIN fpo_profiles fp ON fp.user_id=u.id ORDER BY u.id DESC")
    bulks = query(
        "SELECT u.*, bp.buyer_type, bp.city, bp.verification_status "
        "FROM users u JOIN bulk_buyer_profiles bp ON bp.user_id=u.id ORDER BY u.id DESC")
    demand_analytics = get_demand_analytics()
    return render_template("admin.html", stats=stats, recent=recent,
                           products=prods, users=users, fpos=fpos, bulks=bulks,
                           demand_analytics=demand_analytics)


@app.route("/admin/product/<int:pid>/delete", methods=["POST"])
@role_required("admin")
def admin_product_delete(pid):
    product = query("SELECT * FROM products WHERE id=:id", {"id": pid}, one=True)
    if product:
        _delete_product(product)
        flash("Product deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/fpo/dashboard")
@role_required("fpo")
def fpo_dashboard():
    fid = session["user_id"]
    tab = request.args.get("tab", "overview")
    profile = query("SELECT * FROM fpo_profiles WHERE user_id=:fid", {"fid": fid}, one=True)
    stats = {
        "products": query("SELECT COUNT(*) AS n FROM products WHERE farmer_id=:fid",
                          {"fid": fid}, one=True)["n"],
        "members": query("SELECT COUNT(*) AS n FROM fpo_members WHERE fpo_id=:fid "
                         "AND status='approved'", {"fid": fid}, one=True)["n"],
        "quotes": query("SELECT COUNT(*) AS n FROM bulk_quotes WHERE fpo_id=:fid",
                        {"fid": fid}, one=True)["n"],
        "open_reqs": query("SELECT COUNT(*) AS n FROM bulk_requirements "
                           "WHERE status='Open'", one=True)["n"],
        "bulk_orders": query("SELECT COUNT(*) AS n FROM bulk_orders WHERE fpo_id=:fid "
                             "AND status='Active'", {"fid": fid}, one=True)["n"],
    }
    sup = _fpo_supply_stats(fid)
    stats["agg_available"] = sup["available"]
    stats["agg_committed"] = sup["committed"]
    stats["agg_pending_quotes"] = sup["pending"]
    stats["agg_active"] = sup["active"]
    data = {}
    if tab == "products":
        data["products"] = query(
            "SELECT * FROM products WHERE farmer_id=:fid ORDER BY id DESC", {"fid": fid})
    elif tab == "members":
        data["members_rows"] = query(
            "SELECT fm.*, u.name, u.email, u.phone FROM fpo_members fm "
            "JOIN users u ON u.id=fm.farmer_id WHERE fm.fpo_id=:fid "
            "ORDER BY fm.id DESC", {"fid": fid})
        data["available_farmers"] = query(
            "SELECT * FROM users WHERE role='farmer' AND id NOT IN "
            "(SELECT farmer_id FROM fpo_members WHERE fpo_id=:fid) ORDER BY name", {"fid": fid})
    elif tab == "requests":
        reqs = query(
            "SELECT r.*, u.name AS buyer_name, "
            "(SELECT COUNT(*) FROM bulk_quotes q WHERE q.requirement_id=r.id) AS quote_count, "
            "(SELECT COUNT(*) FROM bulk_quotes q WHERE q.requirement_id=r.id "
            "AND q.fpo_id=:fid) AS my_quotes "
            "FROM bulk_requirements r JOIN users u ON u.id=r.buyer_id "
            "WHERE r.status='Open' ORDER BY r.id DESC", {"fid": fid})
        rows = []
        for r in reqs:
            d = dict(r)
            matches = _matching_supplies(fid, d)
            d["matches"] = matches
            avail = sum(m["on_hand"] for m in matches)
            d["match_total"] = avail
            d["match_status"] = ("complete" if avail >= d["quantity"]
                                 else "partial" if avail > 0 else "none")
            rows.append(d)
        data["requirements"] = rows
        data["my_supply"] = _fpo_aggregate_summary(fid)
    elif tab == "supply":
        data["supplies"] = _fpo_aggregate_summary(fid)
    elif tab == "quotes":
        data["quotes"] = query(
            "SELECT q.*, r.title, r.unit, r.quantity AS req_qty, r.status AS req_status, "
            "u.name AS buyer_name FROM bulk_quotes q "
            "JOIN bulk_requirements r ON r.id=q.requirement_id "
            "JOIN users u ON u.id=r.buyer_id WHERE q.fpo_id=:fid ORDER BY q.id DESC", {"fid": fid})
    elif tab == "orders":
        all_orders = query(
            "SELECT bo.*, u.name AS buyer_name, p.name AS supply_name, p.unit AS supply_unit "
            "FROM bulk_orders bo JOIN users u ON u.id=bo.buyer_id "
            "LEFT JOIN products p ON p.id=bo.supply_id "
            "WHERE bo.fpo_id=:fid ORDER BY bo.id DESC", {"fid": fid})
        rows = []
        for o in all_orders:
            d = dict(o)
            d["sources"] = (_bulk_consumption(d["id"])
                            if d["supply_id"] else [])
            rows.append(d)
        data["bulk_orders"] = rows
    elif tab == "sales":
        earn_where = f"o.status IN ({','.join(f':es{i}' for i in range(len(FARMER_EARNING_STATUSES)))})"
        base = {f"es{i}": s for i, s in enumerate(FARMER_EARNING_STATUSES)}
        base["fid"] = fid
        totals = query(
            f"SELECT COUNT(DISTINCT o.id) AS orders, "
            f"COALESCE(SUM(oi.quantity*oi.price),0) AS revenue "
            f"FROM order_items oi JOIN orders o ON oi.order_id=o.id "
            f"WHERE oi.farmer_id=:fid AND {earn_where}", base, one=True)
        month_rev = query(
            f"SELECT COALESCE(SUM(oi.quantity*oi.price),0) AS s FROM order_items oi "
            f"JOIN orders o ON oi.order_id=o.id WHERE oi.farmer_id=:fid "
            f"AND strftime('%Y-%m', o.created_at)=strftime('%Y-%m','now') AND {earn_where}",
            base, one=True)["s"]
        top_products = query(
            "SELECT oi.product_name, SUM(oi.quantity) AS qty, SUM(oi.quantity*oi.price) AS rev "
            "FROM order_items oi JOIN orders o ON oi.order_id=o.id "
            "WHERE oi.farmer_id=:fid AND o.status!='Cancelled' "
            "GROUP BY oi.product_name ORDER BY rev DESC LIMIT 5", {"fid": fid})
        recent_orders = _farmer_order_groups(fid)[:5]
        avg_order = round(totals["revenue"] / totals["orders"], 2) if totals["orders"] else 0
        data.update(totals=totals, month_rev=month_rev, avg_order=avg_order,
                    top_products=top_products, recent_orders=recent_orders)
    elif tab == "demand":
        demand_insight = get_fpo_demand_insight(fid)
        data["demand_insight"] = demand_insight
    return render_template("fpo_dashboard.html", tab=tab, profile=profile,
                           stats=stats, **data)


def _parse_mrp(value):
    try:
        return float(value) if str(value).strip() else None
    except ValueError:
        return None


@app.route("/fpo/supply/new", methods=["GET", "POST"])
@role_required("fpo")
def fpo_supply_new():
    fid = session["user_id"]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            price = float(request.form.get("price", 0) or 0)
            min_qty = float(request.form.get("min_qty", 1) or 1)
        except ValueError:
            price = min_qty = 0
        unit = request.form.get("unit", "kg")
        category = request.form.get("category", "Vegetables")
        description = request.form.get("description", "").strip()
        if not name or price <= 0:
            flash("Enter a name and a price above 0.", "error")
            return render_template("fpo_supply_form.html", product=None,
                                   units=UNITS, categories=CATEGORIES)
        min_qty = max(int(min_qty), 1)
        if unit not in UNITS:
            unit = "kg"
        if category not in CATEGORIES:
            category = "Vegetables"
        p = Product(farmer_id=fid, name=name, description=description, price=price,
                    mrp=_parse_mrp(request.form.get("mrp")), category=category,
                    unit=unit, stock=0, is_aggregate=1, min_qty=min_qty)
        db.session.add(p)
        db.session.commit()
        flash("Aggregate supply listing created. Add member-farmer allocations to start pooling.",
              "success")
        return redirect(url_for("fpo_supply_detail", sid=p.id))
    return render_template("fpo_supply_form.html", product=None,
                           units=UNITS, categories=CATEGORIES)


@app.route("/fpo/supply/<int:sid>")
@role_required("fpo")
def fpo_supply_detail(sid):
    fid = session["user_id"]
    supply = _fpo_supply(fid, sid)
    if not supply:
        flash("Supply listing not found.", "error")
        return redirect(url_for("fpo_dashboard", tab="supply"))
    sources = _aggregate_sources(sid)
    allocated_ids = {a["product_id"] for a in sources}
    idle_members = []
    for p in _member_products(fid):
        if p["id"] in allocated_ids:
            continue
        committed = _committed_total(p["id"])
        idle_members.append({"id": p["id"], "name": p["name"], "unit": p["unit"],
                             "stock": p["stock"],
                             "left": p["stock"] - committed})
    idle_members = [m for m in idle_members if m["left"] > 0]
    return render_template("fpo_supply_detail.html", supply=supply, sources=sources,
                           idle_members=idle_members,
                           consumption=_aggregate_consumption(sid))


@app.route("/fpo/supply/<int:sid>/allocation/add", methods=["POST"])
@role_required("fpo")
def fpo_supply_allocation_add(sid):
    fid = session["user_id"]
    if not _fpo_supply(fid, sid):
        flash("Supply listing not found.", "error")
        return redirect(url_for("fpo_dashboard", tab="supply"))
    try:
        pid = int(request.form.get("product_id", 0) or 0)
        qty = float(request.form.get("committed_qty", 0) or 0)
    except ValueError:
        pid = qty = 0
    member = query("SELECT * FROM products WHERE id=:id AND is_aggregate=0", {"id": pid}, one=True)
    if not member or member["farmer_id"] not in _approved_member_farmer_ids(fid):
        flash("Pick a product from one of your approved member farmers.", "error")
    elif qty <= 0:
        flash("Committed quantity must be above 0.", "error")
    elif _committed_total(pid) + qty > member["stock"]:
        flash(f"Only {int(member['stock'] - _committed_total(pid))} units of this "
              "member product remain uncommitted.", "error")
    else:
        db.session.execute(text("INSERT INTO supply_allocations (supply_id, product_id, committed_qty) "
                           "VALUES (:sid, :pid, :qty)"), {"sid": sid, "pid": pid, "qty": qty})
        db.session.commit()
        _refresh_aggregate(sid)
        db.session.commit()
        flash("Allocation added.", "success")
    return redirect(url_for("fpo_supply_detail", sid=sid))


@app.route("/fpo/supply/<int:sid>/allocation/<int:aid>/update", methods=["POST"])
@role_required("fpo")
def fpo_supply_allocation_update(sid, aid):
    fid = session["user_id"]
    if not _fpo_supply(fid, sid):
        flash("Supply listing not found.", "error")
        return redirect(url_for("fpo_dashboard", tab="supply"))
    row = query(
        "SELECT a.id, a.product_id, a.committed_qty, p.stock AS stock FROM supply_allocations a "
        "JOIN products p ON p.id=a.product_id WHERE a.supply_id=:sid AND a.id=:aid",
        {"sid": sid, "aid": aid}, one=True)
    if not row:
        flash("Allocation not found.", "error")
    else:
        try:
            qty = float(request.form.get("committed_qty", 0) or 0)
        except ValueError:
            qty = 0
        other = _committed_total(row["product_id"]) - row["committed_qty"]
        if qty <= 0:
            flash("Committed quantity must be above 0.", "error")
        elif other + qty > row["stock"]:
            flash("Committed quantity exceeds what the member farmer still has "
                  f"({int(row['stock'] - other)} left).", "error")
        else:
            db.session.execute(text("UPDATE supply_allocations SET committed_qty=:qty WHERE id=:id"),
                               {"qty": qty, "id": aid})
            db.session.commit()
            _refresh_aggregate(sid)
            db.session.commit()
            flash("Allocation updated.", "success")
    return redirect(url_for("fpo_supply_detail", sid=sid))


@app.route("/fpo/supply/<int:sid>/allocation/<int:aid>/remove", methods=["POST"])
@role_required("fpo")
def fpo_supply_allocation_remove(sid, aid):
    fid = session["user_id"]
    if not _fpo_supply(fid, sid):
        flash("Supply listing not found.", "error")
        return redirect(url_for("fpo_dashboard", tab="supply"))
    db.session.execute(text("DELETE FROM supply_consumption WHERE supply_id=:sid AND allocation_id=:aid"),
                       {"sid": sid, "aid": aid})
    db.session.execute(text("DELETE FROM supply_allocations WHERE id=:aid AND supply_id=:sid"),
                       {"aid": aid, "sid": sid})
    db.session.commit()
    _refresh_aggregate(sid)
    db.session.commit()
    flash("Allocation removed.", "success")
    return redirect(url_for("fpo_supply_detail", sid=sid))


@app.route("/fpo/supply/<int:sid>/edit", methods=["GET", "POST"])
@role_required("fpo")
def fpo_supply_edit(sid):
    fid = session["user_id"]
    supply = _fpo_supply(fid, sid)
    if not supply:
        flash("Supply listing not found.", "error")
        return redirect(url_for("fpo_dashboard", tab="supply"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            price = float(request.form.get("price", 0) or 0)
            min_qty = float(request.form.get("min_qty", 1) or 1)
        except ValueError:
            price = min_qty = 0
        unit = request.form.get("unit", "kg")
        category = request.form.get("category", "Vegetables")
        description = request.form.get("description", "").strip()
        if not name or price <= 0:
            flash("Enter a name and a price above 0.", "error")
            return render_template("fpo_supply_form.html", product=supply,
                                   units=UNITS, categories=CATEGORIES)
        min_qty = max(int(min_qty), 1)
        if unit not in UNITS:
            unit = "kg"
        if category not in CATEGORIES:
            category = "Vegetables"
        db.session.execute(text("UPDATE products SET name=:name, description=:desc, price=:price, "
                           "mrp=:mrp, category=:cat, unit=:unit, min_qty=:mq WHERE id=:id AND is_aggregate=1"),
                           {"name": name, "desc": description, "price": price,
                            "mrp": _parse_mrp(request.form.get("mrp")), "cat": category,
                            "unit": unit, "mq": min_qty, "id": sid})
        db.session.commit()
        flash("Supply listing updated.", "success")
        return redirect(url_for("fpo_supply_detail", sid=sid))
    return render_template("fpo_supply_form.html", product=supply,
                           units=UNITS, categories=CATEGORIES)


@app.route("/fpo/supply/<int:sid>/delete", methods=["POST"])
@role_required("fpo")
def fpo_supply_delete(sid):
    fid = session["user_id"]
    if not _fpo_supply(fid, sid):
        flash("Supply listing not found.", "error")
        return redirect(url_for("fpo_dashboard", tab="supply"))
    db.session.execute(text("DELETE FROM supply_consumption WHERE supply_id=:sid"), {"sid": sid})
    db.session.execute(text("DELETE FROM supply_allocations WHERE supply_id=:sid"), {"sid": sid})
    db.session.execute(text("DELETE FROM cart_items WHERE product_id=:sid"), {"sid": sid})
    db.session.execute(text("DELETE FROM products WHERE id=:sid"), {"sid": sid})
    db.session.commit()
    flash("Supply listing deleted.", "success")
    return redirect(url_for("fpo_dashboard", tab="supply"))


@app.route("/bulk-buyer/dashboard")
@role_required("bulk_buyer")
def bulk_dashboard():
    uid = session["user_id"]
    tab = request.args.get("tab", "overview")
    profile = query("SELECT * FROM bulk_buyer_profiles WHERE user_id=:uid", {"uid": uid}, one=True)
    stats = {
        "requirements": query(
            "SELECT COUNT(*) AS n FROM bulk_requirements WHERE buyer_id=:uid "
            "AND status IN ('Open','Awarded')", {"uid": uid}, one=True)["n"],
        "quotes": query(
            "SELECT COUNT(*) AS n FROM bulk_quotes q JOIN bulk_requirements r "
            "ON r.id=q.requirement_id WHERE r.buyer_id=:uid", {"uid": uid}, one=True)["n"],
        "active_orders": query(
            "SELECT COUNT(*) AS n FROM bulk_orders WHERE buyer_id=:uid AND status='Active'",
            {"uid": uid}, one=True)["n"],
        "fpos_verified": query(
            "SELECT COUNT(*) AS n FROM fpo_profiles WHERE verification_status='verified'",
            one=True)["n"],
    }
    data = {}
    if tab == "find":
        data["fpos"] = query(
            "SELECT u.id AS uid, u.name, fp.* FROM fpo_profiles fp "
            "JOIN users u ON u.id=fp.user_id "
            "WHERE fp.verification_status='verified' ORDER BY u.name")
    elif tab == "requests":
        data["requirements"] = query(
            "SELECT r.*, (SELECT COUNT(*) FROM bulk_quotes q "
            "WHERE q.requirement_id=r.id) AS quote_count "
            "FROM bulk_requirements r WHERE r.buyer_id=:uid ORDER BY r.id DESC", {"uid": uid})
    elif tab == "quotes":
        all_quotes = query(
            "SELECT q.*, r.title, r.unit, r.quantity AS req_qty, r.status AS req_status, "
            "u.name AS fpo_name, p.name AS supply_name "
            "FROM bulk_quotes q JOIN bulk_requirements r ON r.id=q.requirement_id "
            "JOIN users u ON u.id=q.fpo_id LEFT JOIN products p ON p.id=q.supply_id "
            "WHERE r.buyer_id=:uid AND r.status!='Cancelled' ORDER BY q.id DESC", {"uid": uid})
        rows = []
        for q in all_quotes:
            d = dict(q)
            d["fulfill"] = "full" if d["quantity"] >= d["req_qty"] else "partial"
            rows.append(d)
        data["quotes"] = rows
    elif tab == "orders":
        data["bulk_orders"] = query(
            "SELECT bo.*, u.name AS fpo_name, p.name AS supply_name "
            "FROM bulk_orders bo JOIN users u ON u.id=bo.fpo_id "
            "LEFT JOIN products p ON p.id=bo.supply_id "
            "WHERE bo.buyer_id=:uid ORDER BY bo.id DESC", {"uid": uid})
    data["units"] = UNITS
    return render_template("bulk_dashboard.html", tab=tab, profile=profile,
                           stats=stats, **data)


@app.route("/bulk-requirement", methods=["POST"])
@role_required("bulk_buyer")
def bulk_requirement_new():
    title = request.form.get("title", "").strip()
    qty_raw = request.form.get("quantity", "")
    unit = request.form.get("unit", "kg").strip()
    category = request.form.get("category", "Vegetables")
    target_raw = request.form.get("target_price", "").strip()
    if not title:
        flash("Requirement title is required.", "error")
        return redirect(url_for("bulk_dashboard", tab="create"))
    try:
        qty = float(qty_raw)
    except ValueError:
        qty = 0
    if qty <= 0:
        flash("Requirement quantity must be greater than zero.", "error")
        return redirect(url_for("bulk_dashboard", tab="create"))
    if unit not in UNITS:
        unit = "kg"
    if category not in CATEGORIES:
        category = "Vegetables"
    if target_raw:
        try:
            target = float(target_raw)
        except ValueError:
            target = None
        if target is not None and target <= 0:
            target = None
    else:
        target = None
    req = BulkRequirement(
        buyer_id=session["user_id"], title=title,
        description=request.form.get("description", "").strip(),
        category=category, quantity=qty, unit=unit, target_price=target,
        city=request.form.get("city", "").strip(),
        pincode="".join(ch for ch in request.form.get("pincode", ""))[:10])
    db.session.add(req)
    db.session.commit()
    flash("Bulk requirement published. FPOs can now submit quotes.", "success")
    return redirect(url_for("bulk_dashboard", tab="requests"))


@app.route("/bulk-requirement/<int:rid>/cancel", methods=["POST"])
@role_required("bulk_buyer")
def bulk_requirement_cancel(rid):
    r = query("SELECT * FROM bulk_requirements WHERE id=:id AND buyer_id=:uid",
              {"id": rid, "uid": session["user_id"]}, one=True)
    if r and r["status"] == "Open":
        db.session.execute(text("UPDATE bulk_requirements SET status='Cancelled' WHERE id=:id"),
                           {"id": rid})
        db.session.commit()
        flash("Requirement cancelled.", "success")
    return redirect(url_for("bulk_dashboard", tab="requests"))


@app.route("/fpo/quote/<int:rid>", methods=["POST"])
@role_required("fpo")
def fpo_quote_new(rid):
    fid = session["user_id"]
    req = query("SELECT * FROM bulk_requirements WHERE id=:id", {"id": rid}, one=True)
    if not req or req["status"] != "Open":
        flash("Requirement not open for quotes.", "error")
        return redirect(url_for("fpo_dashboard", tab="requests"))
    exists = query("SELECT id FROM bulk_quotes WHERE requirement_id=:rid AND fpo_id=:fid",
                   {"rid": rid, "fid": fid}, one=True)
    if exists:
        flash("You have already quoted on this requirement. Withdraw it first.", "error")
        return redirect(url_for("fpo_dashboard", tab="requests"))
    try:
        sid = int(request.form.get("supply_id", 0) or 0)
    except ValueError:
        sid = 0
    supply = _fpo_supply(fid, sid)
    matches = _matching_supplies(fid, req)
    if not supply or not any(m["id"] == sid for m in matches):
        flash("Pick one of your matching aggregate supply listings.", "error")
        return redirect(url_for("fpo_dashboard", tab="requests"))
    try:
        price = float(request.form.get("price", ""))
    except ValueError:
        price = 0
    if price <= 0:
        flash("Please enter a valid per-unit price.", "error")
        return redirect(url_for("fpo_dashboard", tab="requests"))
    try:
        qty = float(request.form.get("quantity", ""))
    except ValueError:
        qty = 0
    if qty <= 0:
        flash("Please enter a quote quantity above 0.", "error")
        return redirect(url_for("fpo_dashboard", tab="requests"))
    available = _aggregate_on_hand(sid)
    if qty > available:
        flash(f"Cannot quote {qty:g} {req['unit']}. Your current available supply is "
              f"{available:g} {req['unit']}.", "error")
        return redirect(url_for("fpo_dashboard", tab="requests"))
    if qty > req["quantity"]:
        flash(f"The requirement asks for only {req['quantity']:g} {req['unit']}. "
              "Quote at or below that.", "error")
        return redirect(url_for("fpo_dashboard", tab="requests"))
    quote = BulkQuote(
        requirement_id=rid, fpo_id=fid, price=price, quantity=qty,
        notes=request.form.get("notes", "").strip(), supply_id=sid)
    db.session.add(quote)
    db.session.commit()
    flash("Quote submitted to the buyer.", "success")
    return redirect(url_for("fpo_dashboard", tab="quotes"))


@app.route("/fpo/quote/<int:qid>/cancel", methods=["POST"])
@role_required("fpo")
def fpo_quote_cancel(qid):
    q = query("SELECT * FROM bulk_quotes WHERE id=:id AND fpo_id=:fid",
              {"id": qid, "fid": session["user_id"]}, one=True)
    if q and q["status"] == "Pending":
        db.session.execute(text("UPDATE bulk_quotes SET status='Rejected' WHERE id=:id"), {"id": qid})
        db.session.commit()
        flash("Quote withdrawn.", "success")
    else:
        flash("Quote not found.", "error")
    return redirect(url_for("fpo_dashboard", tab="quotes"))


@app.route("/fpo/member/add", methods=["POST"])
@role_required("fpo")
def fpo_member_add():
    fid = session["user_id"]
    try:
        farmer_id = int(request.form.get("farmer_id", ""))
    except (TypeError, ValueError):
        farmer_id = 0
    farmer = query("SELECT * FROM users WHERE id=:id AND role='farmer'",
                   {"id": farmer_id}, one=True)
    if not farmer:
        flash("Select a valid farmer.", "error")
        return redirect(url_for("fpo_dashboard", tab="members"))
    exists = query("SELECT id FROM fpo_members WHERE fpo_id=:fid AND farmer_id=:farmer_id",
                   {"fid": fid, "farmer_id": farmer_id}, one=True)
    if exists:
        flash("That farmer is already a member.", "error")
        return redirect(url_for("fpo_dashboard", tab="members"))
    db.session.execute(text("INSERT INTO fpo_members (fpo_id, farmer_id, status) VALUES (:fid, :farmer_id, 'approved')"),
                       {"fid": fid, "farmer_id": farmer_id})
    db.session.commit()
    flash("Farmer added to your FPO.", "success")
    return redirect(url_for("fpo_dashboard", tab="members"))


@app.route("/fpo/member/<int:mid>/remove", methods=["POST"])
@role_required("fpo")
def fpo_member_remove(mid):
    row = query("SELECT * FROM fpo_members WHERE id=:id AND fpo_id=:fid",
                {"id": mid, "fid": session["user_id"]}, one=True)
    if row:
        db.session.execute(text("DELETE FROM fpo_members WHERE id=:id"), {"id": mid})
        db.session.commit()
        flash("Farmer removed from your FPO.", "success")
    return redirect(url_for("fpo_dashboard", tab="members"))


@app.route("/bulk/quote/<int:qid>/<verb>", methods=["POST"])
@role_required("bulk_buyer")
def bulk_quote_respond(qid, verb):
    if verb not in ("accept", "reject"):
        abort(404)
    q = query(
        "SELECT q.*, r.buyer_id AS rb, r.title, r.status AS req_status, "
        "r.quantity AS req_qty, r.unit AS req_unit "
        "FROM bulk_quotes q JOIN bulk_requirements r ON r.id=q.requirement_id "
        "WHERE q.id=:qid", {"qid": qid}, one=True)
    if not q or q["rb"] != session["user_id"]:
        flash("Quote not found.", "error")
        return redirect(url_for("bulk_dashboard", tab="quotes"))
    if q["status"] != "Pending":
        flash("This quote is no longer pending.", "error")
        return redirect(url_for("bulk_dashboard", tab="quotes"))
    if verb == "reject":
        db.session.execute(text("UPDATE bulk_quotes SET status='Rejected' WHERE id=:qid AND status='Pending'"),
                           {"qid": qid})
        db.session.commit()
        from services.notifications import notify_quote_event
        notify_quote_event(db.session, qid, "rejected", q["fpo_id"], session["user_id"], q["title"])
        flash("Quote rejected.", "success")
        return redirect(url_for("bulk_dashboard", tab="quotes"))
    if q["req_status"] != "Open":
        flash("This requirement is no longer open for acceptance.", "error")
        return redirect(url_for("bulk_dashboard", tab="quotes"))
    try:
        supply = None
        if q["supply_id"]:
            supply = query(
                "SELECT * FROM products WHERE id=:id AND is_aggregate=1 AND farmer_id=:fid",
                {"id": q["supply_id"], "fid": q["fpo_id"]}, one=True)
            if not supply:
                raise ValueError("The aggregate supply backing this quote no longer exists.")
            on_hand = _aggregate_on_hand(supply["id"])
            if q["quantity"] > on_hand:
                raise ValueError(
                    f"Cannot accept: only {on_hand:g} {q['req_unit']} of this supply is still "
                    f"available, but the quote needs {q['quantity']:g} {q['req_unit']}.")
        addr = query("SELECT address FROM users WHERE id=:id", {"id": session["user_id"]}, one=True)
        bo = BulkOrder(
            requirement_id=q["requirement_id"], quote_id=qid,
            buyer_id=session["user_id"], fpo_id=q["fpo_id"], title=q["title"],
            quantity=q["quantity"], price=q["price"],
            total=round(q["quantity"] * q["price"], 2),
            delivery_address=addr["address"] if addr and addr["address"] else "",
            supply_id=q["supply_id"])
        db.session.add(bo)
        db.session.flush()
        oid = bo.id
        if supply is not None:
            _consume_aggregate_bulk(supply, q["quantity"], oid)
        db.session.execute(text("UPDATE bulk_quotes SET status='Accepted' WHERE id=:qid AND status='Pending'"),
                           {"qid": qid})
        db.session.execute(text("UPDATE bulk_quotes SET status='Rejected' "
                           "WHERE requirement_id=:rid AND id<>:qid AND status='Pending'"),
                           {"rid": q["requirement_id"], "qid": qid})
        db.session.execute(text("UPDATE bulk_requirements SET status='Awarded' WHERE id=:rid AND status='Open'"),
                           {"rid": q["requirement_id"]})
        db.session.commit()
        from services.notifications import notify_quote_event
        notify_quote_event(db.session, qid, "accepted", q["fpo_id"], session["user_id"], q["title"])
        flash("Quote accepted. A bulk order was created and supply reserved.", "success")
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception as e:
        db.session.rollback()
        app.logger.error("quote accept failed: %s", e)
        flash("Could not accept the quote. Please try again.", "error")
    return redirect(url_for("bulk_dashboard", tab="quotes"))


@app.route("/bulk-order/<int:oid>/<verb>", methods=["POST"])
@role_required("bulk_buyer")
def bulk_order_respond(oid, verb):
    if verb not in ("complete", "cancel"):
        abort(404)
    bo = query("SELECT * FROM bulk_orders WHERE id=:id", {"id": oid}, one=True)
    if not bo or bo["buyer_id"] != session["user_id"]:
        flash("Order not found.", "error")
        return redirect(url_for("bulk_dashboard", tab="orders"))
    target = "Completed" if verb == "complete" else "Cancelled"
    if bo["status"] == "Active":
        if target == "Cancelled":
            _restore_aggregate_bulk(oid)
        db.session.execute(text("UPDATE bulk_orders SET status=:status WHERE id=:id"),
                           {"status": target, "id": oid})
        if target == "Completed":
            db.session.execute(text("UPDATE bulk_requirements SET status='Closed' WHERE id=:rid"),
                               {"rid": bo["requirement_id"]})
        else:
            db.session.execute(text("UPDATE bulk_requirements SET status='Open' WHERE id=:rid"),
                               {"rid": bo["requirement_id"]})
        db.session.commit()
        flash(f"Bulk order #{oid} updated to '{target}'.", "success")
    return redirect(url_for("bulk_dashboard", tab="orders"))


@app.route("/admin/<kind>/<int:uid>/<verb>", methods=["POST"])
@role_required("admin")
def admin_verify(kind, uid, verb):
    if kind not in ("fpo", "bulk", "logistics") or verb not in ("verify", "reject"):
        abort(404)
    table_map = {"fpo": "fpo_profiles", "bulk": "bulk_buyer_profiles",
                 "logistics": "logistics_profiles"}
    table = table_map[kind]
    profile = query(f"SELECT * FROM {table} WHERE user_id=:uid", {"uid": uid}, one=True)
    if not profile:
        flash("Profile not found.", "error")
        return redirect(url_for("admin"))
    new_status = "verified" if verb == "verify" else "rejected"
    db.session.execute(text(f"UPDATE {table} SET verification_status=:status WHERE user_id=:uid"),
                       {"status": new_status, "uid": uid})
    user = User.query.get(uid)
    if user:
        user.is_verified = (verb == "verify")
    db.session.commit()
    flash(f"Verification marked as '{new_status}'.", "success")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Demand Forecast API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/demand-forecast")
def api_demand_forecast_all():
    forecasts = generate_all_forecasts()
    return {"forecasts": forecasts, "count": len(forecasts)}


@app.route("/api/demand-forecast/<product_name>")
def api_demand_forecast_product(product_name):
    fc = generate_forecast(product_name)
    return fc


@app.route("/api/demand-analytics")
def api_demand_analytics():
    return get_demand_analytics()


@app.route("/admin/demand")
@role_required("admin")
def admin_demand():
    analytics = get_demand_analytics()
    return render_template("admin_demand.html", analytics=analytics)


# ---------------------------------------------------------------------------
# Logistics Management Routes
# ---------------------------------------------------------------------------

@app.route("/logistics/dashboard")
@role_required("logistics")
def logistics_dashboard():
    from services.logistics import (
        get_all_vehicles,
        get_assignments_for_logistics,
        get_logistics_stats,
    )
    user = current_user()
    stats = get_logistics_stats(db.session, user["id"])
    assignments = get_assignments_for_logistics(db.session, user["id"])
    vehicles = get_all_vehicles(db.session, user["id"])
    from services.notifications import get_notifications
    notifs = get_notifications(db.session, user["id"], limit=20)
    return render_template("logistics_dashboard.html",
                           stats=stats, assignments=assignments,
                           vehicles=vehicles, notifications=notifs)


@app.route("/logistics/vehicle/new", methods=["GET", "POST"])
@role_required("logistics")
def logistics_vehicle_new():
    from services.logistics import create_vehicle
    if request.method == "POST":
        vtype = request.form.get("vehicle_type", "Truck")
        reg = request.form.get("registration_number", "").strip()
        dname = request.form.get("driver_name", "").strip()
        dphone = request.form.get("driver_phone", "").strip()
        try:
            cap = float(request.form.get("capacity_kg", 0))
        except ValueError:
            cap = 0
        if cap <= 0:
            flash("Capacity must be greater than 0.", "error")
            return redirect(url_for("logistics_vehicle_new"))
        create_vehicle(db.session, session["user_id"], vtype, reg, dname, dphone, cap)
        flash("Vehicle added successfully.", "success")
        return redirect(url_for("logistics_dashboard", tab="vehicles"))
    return render_template("logistics_vehicle_form.html")


@app.route("/logistics/vehicle/<int:vid>/edit", methods=["GET", "POST"])
@role_required("logistics")
def logistics_vehicle_edit(vid):
    from services.logistics import update_vehicle
    vehicle = query("SELECT * FROM vehicles WHERE id=:vid AND logistics_id=:lid",
                    {"vid": vid, "lid": session["user_id"]}, one=True)
    if not vehicle:
        flash("Vehicle not found.", "error")
        return redirect(url_for("logistics_dashboard"))
    if request.method == "POST":
        kwargs = {
            "vehicle_type": request.form.get("vehicle_type", vehicle["vehicle_type"]),
            "registration_number": request.form.get("registration_number", "").strip(),
            "driver_name": request.form.get("driver_name", "").strip(),
            "driver_phone": request.form.get("driver_phone", "").strip(),
            "is_available": 1 if request.form.get("is_available") else 0,
        }
        try:
            kwargs["capacity_kg"] = float(request.form.get("capacity_kg", vehicle["capacity_kg"]))
        except ValueError:
            kwargs["capacity_kg"] = vehicle["capacity_kg"]
        update_vehicle(db.session, vid, session["user_id"], **kwargs)
        flash("Vehicle updated.", "success")
        return redirect(url_for("logistics_dashboard", tab="vehicles"))
    return render_template("logistics_vehicle_form.html", vehicle=vehicle)


@app.route("/logistics/vehicle/<int:vid>/delete", methods=["POST"])
@role_required("logistics")
def logistics_vehicle_delete(vid):
    from services.logistics import delete_vehicle
    ok, msg = delete_vehicle(db.session, vid, session["user_id"])
    flash(msg, "success" if ok else "error")
    return redirect(url_for("logistics_dashboard", tab="vehicles"))


@app.route("/logistics/assignment/<int:aid>/update", methods=["POST"])
@role_required("logistics")
def logistics_assignment_update(aid):
    from services.logistics import get_assignment, update_assignment_status
    from services.notifications import notify_delivery_event
    new_status = request.form.get("status", "")
    ok, msg = update_assignment_status(db.session, aid, new_status, session["user_id"])
    if ok:
        assignment = get_assignment(db.session, aid)
        if assignment and assignment["order_id"]:
            event_map = {"Picked Up": "picked_up", "In Transit": "in_transit",
                         "Delivered": "delivered", "Failed": "failed"}
            if new_status in event_map:
                notify_delivery_event(db.session, aid, event_map[new_status],
                                      session["user_id"], assignment["order_id"])
        flash(f"Assignment updated to '{new_status}'.", "success")
    else:
        flash(msg, "error")
    return redirect(url_for("logistics_dashboard", tab="assignments"))


@app.route("/logistics/assignment/<int:aid>")
@role_required("logistics")
def logistics_assignment_detail(aid):
    from services.logistics import get_assignment, get_assignments_for_order
    assignment = get_assignment(db.session, aid)
    if not assignment:
        flash("Assignment not found.", "error")
        return redirect(url_for("logistics_dashboard"))
    if assignment["logistics_id"] != session["user_id"]:
        user = current_user()
        if not user or user["role"] != "admin":
            flash("Access denied.", "error")
            return redirect(url_for("logistics_dashboard"))
    order_assignments = []
    if assignment["order_id"]:
        order_assignments = get_assignments_for_order(db.session, assignment["order_id"])
    return render_template("logistics_assignment_detail.html",
                           assignment=assignment, order_assignments=order_assignments)


@app.route("/api/logistics/assign", methods=["POST"])
@role_required("admin")
def api_logistics_assign():
    from services.logistics import auto_assign_delivery
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id")
    bulk_order_id = data.get("bulk_order_id")
    if not order_id and not bulk_order_id:
        return {"error": "order_id or bulk_order_id required"}, 400

    pickup_address = data.get("pickup_address", "")
    pickup_city = data.get("pickup_city", "")
    pickup_lat = data.get("pickup_latitude")
    pickup_lng = data.get("pickup_longitude")
    dest_address = data.get("destination_address", "")
    dest_city = data.get("destination_city", "")
    dest_lat = data.get("destination_latitude")
    dest_lng = data.get("destination_longitude")
    capacity = data.get("required_capacity_kg", 0)

    assignment, err = auto_assign_delivery(
        db.session, order_id=order_id, bulk_order_id=bulk_order_id,
        pickup_address=pickup_address, pickup_city=pickup_city,
        pickup_latitude=pickup_lat, pickup_longitude=pickup_lng,
        destination_address=dest_address, destination_city=dest_city,
        destination_latitude=dest_lat, destination_longitude=dest_lng,
        required_capacity_kg=capacity)

    if assignment:
        from services.notifications import notify_delivery_event
        notify_delivery_event(db.session, assignment["id"], "assigned",
                              assignment["logistics_id"], order_id)
        return {"assignment_id": assignment["id"], "status": "Assigned"}
    return {"error": err or "Assignment failed"}, 400


@app.route("/api/route/optimize", methods=["POST"])
def api_route_optimize():
    from services.route_optimizer import optimize_delivery_route
    data = request.get_json(silent=True) or {}
    pickups = data.get("pickups", [])
    deliveries = data.get("deliveries", [])
    depot = data.get("depot")

    if not pickups and not deliveries:
        return {"error": "pickups or deliveries required"}, 400

    result = optimize_delivery_route(pickups, deliveries, tuple(depot) if depot else None)
    return result


@app.route("/admin/logistics")
@role_required("admin")
def admin_logistics():
    from services.logistics import get_all_active_deliveries
    deliveries = get_all_active_deliveries(db.session)
    partners = query(
        "SELECT lp.*, u.name, u.email FROM logistics_profiles lp "
        "JOIN users u ON u.id = lp.user_id ORDER BY lp.created_at DESC")
    return render_template("admin_logistics.html",
                           deliveries=deliveries, partners=partners)


@app.route("/admin/logistics/<kind>/<int:uid>/<verb>", methods=["POST"])
@role_required("admin")
def admin_logistics_verify(kind, uid, verb):
    if kind != "logistics" or verb not in ("verify", "reject"):
        abort(404)
    new_status = "verified" if verb == "verify" else "rejected"
    db.session.execute(text("UPDATE logistics_profiles SET verification_status=:status WHERE user_id=:uid"),
                       {"status": new_status, "uid": uid})
    user = User.query.get(uid)
    if user:
        user.is_verified = (verb == "verify")
    db.session.commit()
    flash(f"Logistics partner verification marked as '{new_status}'.", "success")
    return redirect(url_for("admin_logistics"))


@app.route("/notifications")
@login_required
def notifications_page():
    from services.notifications import get_notifications, mark_all_as_read
    user = current_user()
    notifs = get_notifications(db.session, user["id"], limit=100)
    mark_all_as_read(db.session, user["id"])
    return render_template("notifications.html", notifications=notifs)


@app.route("/api/notifications")
@login_required
def api_notifications():
    from services.notifications import get_notifications, get_unread_count
    user = current_user()
    notifs = get_notifications(db.session, user["id"], limit=20)
    count = get_unread_count(db.session, user["id"])
    return {
        "notifications": [dict(n) for n in notifs],
        "unread_count": count,
    }


@app.route("/api/notifications/read/<int:nid>", methods=["POST"])
@login_required
def api_notification_read(nid):
    from services.notifications import mark_as_read
    mark_as_read(db.session, nid, session["user_id"])
    return {"ok": True}


@app.route("/api/alerts")
def api_alerts():
    from services.alerts import get_all_alerts
    alerts = get_all_alerts(db.session)
    return {"alerts": alerts, "count": len(alerts)}


@app.route("/map")
def supply_chain_map():
    return render_template("map.html")


@app.route("/api/map/data")
def api_map_data():
    entities = []

    farmers = query(
        "SELECT id, name, latitude, longitude, city, address FROM users WHERE role='farmer' AND latitude IS NOT NULL")
    for f in farmers:
        entities.append({
            "type": "farmer", "id": f["id"], "name": f["name"],
            "lat": f["latitude"], "lng": f["longitude"],
            "city": f["city"] or "", "address": f["address"] or "",
        })

    fpos = query(
        "SELECT fp.user_id AS id, u.name, fp.latitude, fp.longitude, "
        "fp.city, fp.address, fp.member_count, fp.main_crops "
        "FROM fpo_profiles fp JOIN users u ON u.id = fp.user_id "
        "WHERE fp.latitude IS NOT NULL")
    for f in fpos:
        entities.append({
            "type": "fpo", "id": f["id"], "name": f["name"],
            "lat": f["latitude"], "lng": f["longitude"],
            "city": f["city"] or "", "address": f["address"] or "",
            "member_count": f["member_count"], "crops": f["main_crops"] or "",
        })

    buyers = query(
        "SELECT bp.user_id AS id, u.name, bp.latitude, bp.longitude, "
        "bp.city, bp.address, bp.buyer_type "
        "FROM bulk_buyer_profiles bp JOIN users u ON u.id = bp.user_id "
        "WHERE bp.latitude IS NOT NULL")
    for b in buyers:
        entities.append({
            "type": "buyer", "id": b["id"], "name": b["name"],
            "lat": b["latitude"], "lng": b["longitude"],
            "city": b["city"] or "", "address": b["address"] or "",
            "buyer_type": b["buyer_type"],
        })

    logistics = query(
        "SELECT lp.user_id AS id, u.name, lp.latitude, lp.longitude, "
        "lp.city, lp.address "
        "FROM logistics_profiles lp JOIN users u ON u.id = lp.user_id "
        "WHERE lp.latitude IS NOT NULL AND lp.verification_status='verified'")
    for l in logistics:
        entities.append({
            "type": "logistics", "id": l["id"], "name": l["name"],
            "lat": l["latitude"], "lng": l["longitude"],
            "city": l["city"] or "", "address": l["address"] or "",
        })

    active_deliveries = query(
        "SELECT da.id, da.status, da.pickup_latitude, da.pickup_longitude, "
        "da.destination_latitude, da.destination_longitude, "
        "da.pickup_address, da.destination_address "
        "FROM delivery_assignments da "
        "WHERE da.status NOT IN ('Delivered','Failed','Cancelled') "
        "AND da.pickup_latitude IS NOT NULL")
    for d in active_deliveries:
        entities.append({
            "type": "delivery", "id": d["id"], "status": d["status"],
            "pickup_lat": d["pickup_latitude"], "pickup_lng": d["pickup_longitude"],
            "dest_lat": d["destination_latitude"], "dest_lng": d["destination_longitude"],
            "pickup": d["pickup_address"], "destination": d["destination_address"],
        })

    return {"entities": entities}


@app.route("/api/analytics/farmer/<int:fid>")
@login_required
def api_farmer_analytics(fid):
    from services.analytics import get_farmer_analytics
    user = current_user()
    if not user:
        return {"error": "login_required"}, 401
    if user["role"] != "admin" and user["id"] != fid:
        return {"error": "forbidden"}, 403
    return get_farmer_analytics(db.session, fid)


@app.route("/api/analytics/fpo/<int:fid>")
@login_required
def api_fpo_analytics(fid):
    from services.analytics import get_fpo_analytics
    user = current_user()
    if not user:
        return {"error": "login_required"}, 401
    if user["role"] != "admin" and user["id"] != fid:
        return {"error": "forbidden"}, 403
    return get_fpo_analytics(db.session, fid)


@app.route("/api/analytics/admin")
@role_required("admin")
def api_admin_analytics():
    from services.analytics import get_admin_analytics
    return get_admin_analytics(db.session)


@app.route("/api/pricing/breakdown/<int:oid>")
@login_required
def api_price_breakdown(oid):
    from services.pricing import get_order_price_detail
    user = current_user()
    if not user:
        return {"error": "login_required"}, 401
    if not _can_access_order(user, oid):
        return {"error": "forbidden"}, 403
    result = get_order_price_detail(db.session, oid)
    if not result:
        return {"error": "not_found"}, 404
    return result


@app.route("/admin/intelligence")
@role_required("admin")
def admin_intelligence():
    from services.alerts import get_all_alerts
    from services.analytics import get_admin_analytics
    from services.logistics import get_all_active_deliveries
    analytics = get_admin_analytics(db.session)
    alerts = get_all_alerts(db.session)
    deliveries = get_all_active_deliveries(db.session)
    forecasts = generate_all_forecasts()
    return render_template("admin_intelligence.html",
                           analytics=analytics, alerts=alerts,
                           deliveries=deliveries, forecasts=forecasts)


@app.route("/route-optimizer")
@login_required
def route_optimizer_page():
    from services.logistics import get_all_active_deliveries, get_all_vehicles
    user = current_user()
    vehicles = []
    deliveries = []
    if user["role"] == "logistics":
        vehicles = get_all_vehicles(db.session, user["id"])
        deliveries = get_all_active_deliveries(db.session)
    elif user["role"] == "admin":
        vehicles = query(
            "SELECT v.*, u.name AS logistics_company FROM vehicles v "
            "JOIN users u ON u.id = v.logistics_id WHERE v.is_available=1")
        deliveries = get_all_active_deliveries(db.session)

    stops = []
    for d in deliveries:
        if d["pickup_latitude"] and d["destination_latitude"]:
            load = d["required_capacity_kg"] or 0
            stops.append({
                "type": "pickup",
                "id": d["id"],
                "lat": d["pickup_latitude"],
                "lng": d["pickup_longitude"],
                "label": f"Pickup #{d['id']}",
                "load_kg": load,
            })
            stops.append({
                "type": "delivery",
                "id": d["id"],
                "lat": d["destination_latitude"],
                "lng": d["destination_longitude"],
                "label": d.get("destination_address") or f"Delivery #{d['id']}",
                "load_kg": load,
            })

    return render_template("route_optimizer.html",
                           vehicles=vehicles, deliveries=deliveries,
                           stops=stops,
                           is_logistics=user["role"] == "logistics",
                           is_admin=user["role"] == "admin")


init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
