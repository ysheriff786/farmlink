# FarmLink — Complete Project Guide (A to Z)

A complete, end-to-end explanation of the **FarmLink** farm marketplace
(project folder: `farm-market`). Everything from "what it is" to "how every
table, route, and role behaves" to "how to deploy and test it."

---

## 1. What Is FarmLink?

FarmLink is an online **B2B + B2C marketplace** that connects Indian farmers
and farmer-producer organisations (FPOs) directly with retail customers and
bulk buyers. It removes middlemen so farmers earn fairer prices and buyers get
fresher produce, with **live order tracking** and an optional **logistics
fleet** for delivery.

It is a single-process **Flask** web application backed by **SQLite** (zero
setup) and rendered with **Jinja2** templates.

---

## 2. Who Uses It? (Roles)

| Role | Sign-up fields (extra) | Sandbox account | Home page |
|------|------------------------|-----------------|-----------|
| `farmer` | phone | `y@g.com` / `123456`, `h@g.com` / `123456` | `/dashboard` |
| `fpo` (Farmer Producer Organisation) | registration ID, org address, member count | `f@g.com` / `123456` | `/fpo/dashboard` |
| `customer` | phone | `s@g.com` / `123456`, `guest@g.com` / `123456` | `/products` |
| `bulk_buyer` | buyer type, org address | `b@g.com` / `123456` | `/bulk-buyer/dashboard` |
| `logistics` | company name, org address | `logi@g.com` / `123456` | `/logistics/dashboard` |
| `admin` | (cannot self-register) | `admin@farmlink.com` / `password123` | `/admin` |

> Passwords are hashed (Werkzeug PBKDF2). Only `admin` can be created via the
> `make_admin.py` script.

---

## 3. Feature List (A–Z)

- **Accounts** — register/login/logout, profile editing, role-based redirect
- **Admin panel** — user stats, product moderation, partner verification,
  demand/analytics screen, route planner
- **Aggregate supply pools** (FPO) — pool multiple member-farmers' produce into
  one sellable/quotable listing with shared stock
- **Bulk RFQ (Request for Quote)** — bulk buyers post requirements, FPOs quote,
  buyers accept → bulk order
- **Cart & checkout** — stock-aware cart, min-quantity rules, COD or Razorpay
- **Delivery & logistics** — vehicles, admin auto-assignment, status pipeline,
  customer tracking
- **Demand forecasting** — ML-driven (scikit-learn) forecasts for farmers/FPOs
- **Messaging** — buyer ↔ farmer chat scoped per order
- **Notifications** — in-app notifications for orders, quotes, deliveries, alerts
- **Online payments** — Razorpay (optional; degrades to COD without keys)
- **Order chat + timeline** — live status updates with auto-refresh polling
- **OTP delivery confirmation** — 6-digit code customer confirms before Delivered
- **Price transparency** — per-item gross vs platform-fee vs realized amounts
- **Reviews & ratings** — one review per product per user, editable
- **Search & filters** — category, unit, price sorting
- **Supply chain map** — geo map of farms/origins
- **Tracking** — public order tracking page with access control

---

## 4. Tech Stack

| Layer | Choice |
|-------|--------|
| Backend | Python 3.10+ / Flask 3 |
| Database | SQLite (`farmmarket.db`), `sqlite3` stdlib with `Row` factory |
| Templates | Jinja2 |
| Frontend | Vanilla CSS/JS with a dark-mode toggle |
| Payments | Razorpay (optional) |
| ML / analytics | numpy, pandas, scikit-learn (demand forecasting) |
| Production server | Waitress |

`requirements.txt`:

```
flask>=3.0
waitress>=3.0
numpy>=1.24
pandas>=2.0
scikit-learn>=1.3
```

---

## 5. Project Layout

```
farm-market/
├── app.py                 # Entire app: routes, models, auth, business logic, schema
├── seed.py                # Demo data seeder (7 users, products, FPO pool, RFQs)
├── make_admin.py          # Promote any email to admin
├── requirements.txt
├── render.yaml            # Render.com deployment config
├── .env.example           # SECRET_KEY + Razorpay keys template
├── farmmarket.db          # SQLite database (auto-created)
├── static/
│   ├── style.css          # All styling, light + dark themes
│   ├── script.js          # Nav/cart/tracking interactivity
│   └── uploads/           # Product images
├── templates/
│   ├── base.html          # Master layout: topbar, cat-nav, mega menu, footer
│   ├── index.html         # Landing page
│   ├── products.html      # Store grid + filters
│   ├── product_detail.html
│   ├── cart.html / checkout.html / orders.html / track.html
│   ├── login.html / register.html / profile.html
│   ├── dashboard.html     # Farmer
│   ├── fpo_dashboard.html # FPO
│   ├── bulk_dashboard.html# Bulk buyer
│   ├── logistics_dashboard.html
│   ├── admin.html / admin_intelligence.html / admin_logistics.html
│   ├── route_optimizer.html / map.html / notifications.html
│   └── ...                # product_form, fpo_supply_*, logistics_* etc.
└── services/
    ├── alerts.py          # Stock/ashortage alert rules
    ├── analytics.py       # Farmer / FPO / admin analytics feeds
    ├── demand_forecast.py # scikit-learn forecasting + caching
    ├── location.py        # Haversine distance helpers
    ├── logistics.py       # Vehicles + delivery assignment engine
    ├── matching.py        # FPO supply ↔ RFQ matching
    ├── notifications.py   # In-app notification helpers
    ├── pricing.py         # Platform fees, price breakdowns
    └── route_optimizer.py # Pickup/delivery route solver
```

---

## 6. Getting Started

```bash
cd farm-market
python -m venv venv
venv\Scripts\activate          # Windows  (mac/linux: source venv/bin/activate)
pip install -r requirements.txt
python app.py                  # dev server on http://127.0.0.1:5000
```

Optional demo data (skips if the DB already has users):

```bash
python seed.py
```

Optional: copy `.env.example` → `.env` and set `SECRET_KEY` + Razorpay keys.
Without keys, checkout offers **Cash on Delivery only**.

Make someone an admin:

```bash
python make_admin.py your@email.com   # default password: password123
```

---

## 7. Configuration

`.env` keys loaded by `app.py`:

| Key | Purpose | Default |
|-----|---------|---------|
| `SECRET_KEY` | Signs Flask sessions | `dev-secret-change-me` |
| `FLASK_DEBUG` | `1` = debug mode / template auto-reload | off |
| `RAZORPAY_KEY_ID` | Online payments | empty → COD only |
| `RAZORPAY_KEY_SECRET` | Razorpay signature verification | empty |

Constants in `app.py`:

- `ROLES = farmer, fpo, customer, bulk_buyer, admin, logistics`
- `SELLER_ROLES = farmer, fpo`
- `BUYER_ROLES = customer, bulk_buyer`
- `UNITS = kg, gram, dozen, piece, litre, bunch, pack`
- `CATEGORIES = Vegetables, Fruits, Flowers, Grains & Pulses, Dairy & Eggs, Honey`
- `ORDER STATUSES = Pending Payment, Placed, Confirmed, Out for Delivery, Delivered, Cancelled`

---

## 8. Database Schema & Relationships

Created automatically by `init_db()` from `SCHEMA` in `app.py`.

### Core users & catalogue

- **users** — `id`, `name`, `email` (unique), `password_hash`, `role`, `phone`,
  `address`, `lat/lng`, `city`, `state`, `pincode`
- **products** — `id`, `farmer_id → users`, `name`, `description`, `price`,
  `mrp`, `category`, `unit`, `stock`, `image`, `is_aggregate` (1 = FPO pool),
  `min_qty`

### Retail shopping

- **cart_items** — `customer_id`, `product_id`, `quantity`
- **orders** — `customer_id`, `total`, `status`, `payment_method` (COD/Online),
  `payment_id`, `rzp_order_id`, `delivery_otp`, `address`, `phone`
- **order_items** — snapshot per line: `order_id`, `product_name`, `farmer_id`,
  `quantity`, `price`
- **reviews** — `product_id`, `user_id` (one per user per product), `rating` 1–5, `comment`
- **messages** — buyer↔farmer chat: `order_id`, `sender_id`, `text`
- **subscribers** — newsletter emails

### Organic credentials (FPO)

- **fpo_profiles** — `user_id` (PK→users), `registration_id`, `contact_person`,
  org address, `member_count`, `main_crops`, `verification_status`
- **fpo_members** — `fpo_id`, `farmer_id` (approved members only)
- **supply_allocations** — links an **aggregate supply product** to a member
  farmer's **product** with `committed_qty`; UNIQUE(`supply_id`,`product_id`)
- **supply_consumption** — rows redeemed from allocations for retail line items
  (`order_item_id`) or bulk orders (`bulk_order_id`), with `qty`

### Bulk / B2B

- **bulk_buyer_profiles** — `user_id`, `buyer_type`, org contact, address, `verification_status`
- **bulk_requirements** — buyer RFQ: title, category, `quantity`, `unit`,
  `target_price`, city/pincode; status `Open → Awarded → Closed | Cancelled`
- **bulk_quotes** — FPO quote on a requirement: `fpo_id`, `price`, `quantity`,
  `supply_id` (backing aggregate pool), status `Pending → Accepted | Rejected`
- **bulk_orders** — awarded order: buyer/fpo/quote/supply, `quantity`, `price`,
  `total`, `delivery_address`; status `Active → Completed | Cancelled`

### Logistics

- **logistics_profiles** — `user_id`, `company_name`, `license_number`,
  fleet size, address, `verification_status`
- **vehicles** — `logistics_id`, `vehicle_type`, `registration_number`,
  `driver_name`, `driver_phone`, `capacity_kg`, `is_available`
- **delivery_assignments** — `order_id` or `bulk_order_id`, `logistics_id`,
  `vehicle_id`, pickup/destination addresses + coords, `required_capacity_kg`;
  status pipeline `Assigned → Pickup Pending → Picked Up → In Transit →
  Arrived → Delivered/Failed/Cancelled`

### Notifications

- **notifications** — `user_id`, `type` (info/order/delivery/quote/alert/system),
  `title`, `message`, `reference_type`, `reference_id`, `is_read`
- **alerts service** — rule-based stock/shortage alerts surfaced to farmers/FPOs

---

## 9. Auth & Access Control

- Passwords hashed on register (`generate_password_hash`), verified with
  `check_password_hash` on login.
- Sessions are signed cookies with `SECRET_KEY`; `session["user_id"]` drives
  `current_user()`.
- `login_required` decorator redirects anonymous users to `/login`.
- `role_required(*roles)` rejects users whose role isn't in the list (302).
- Helpers you'll meet in routes:
  - `role_home(user)` — per-role landing page
  - `_can_access_order(user, oid)` — order visible to buyer, involved farmer, admin
  - `_can_change(role, current, target)` — strict status-transition map
- Login `next=` redirect guards open-redirects (only same-site `/...`).

---

## 10. HTTP Routes (API Map)

### Public / visitors
| Method & Path | Purpose |
|---|---|
| GET `/` | Landing page |
| GET `/products` | Store grid (category/unit/query filters) |
| GET `/product/<pid>` | Product detail + reviews |
| POST `/product/<pid>/review` | Add/edit a review |
| GET `/page/<slug>` | Static pages (about/contact/policies) |
| GET `/track` | Order tracking (login required) |
| POST `/subscribe` | Newsletter subscribe |
| GET `/api/live/counts`, `/api/stats` | Nav badges / stats |

### Accounts
| Method & Path | Purpose |
|---|---|
| GET/POST `/register` | Create account (role-gated fields) |
| GET/POST `/login` | Login (role-redirect) |
| GET `/logout` | Logout |
| GET/POST `/profile` | Edit name/phone/address/details |
| GET `/notifications`, `/api/notifications`, `/api/notifications/read/<nid>` | Notifications |

### Retail buying
| Method & Path | Purpose |
|---|---|
| GET `/cart` | View cart (buyer only) |
| POST `/cart/add/<pid>` | Add to cart (stock/min-qty aware) |
| POST `/cart/remove/<item_id>` | Remove line |
| GET `/checkout` | Checkout page |
| POST `/place-order` | Place order (COD or Online) |
| GET `/pay/<oid>` | Razorpay checkout |
| POST `/pay/<oid>/verify` | Verify Razorpay signature |
| GET `/orders` | My orders / farmer's received orders |
| POST `/order/<oid>/status` | Farmer/admin update status (OTP-guarded) |
| GET/POST `/api/order/<oid>/messages` | Per-order chat |

### Farmer selling
| Method & Path | Purpose |
|---|---|
| GET/POST `/product/new`, `/product/<pid>/edit`, POST `/product/<pid>/delete` | CRUD own products |
| GET `/dashboard` | Sales stats, 14-day chart, top products, forecasts |
| GET `/api/analytics/farmer/<fid>` | Analytics feed |
| GET `/api/pricing/breakdown/<oid>` | Fee transparency |

### FPO
| Method & Path | Purpose |
|---|---|
| GET `/fpo/dashboard` | Tabs: products, members, requests, supply, quotes, orders, sales, demand |
| POST `/fpo/member/add`, `/fpo/member/<mid>/remove` | Manage member farmers |
| GET/POST `/fpo/supply/new` | Create aggregate supply |
| GET `/fpo/supply/<sid>` | Supply detail (allocations/consumption) |
| POST `/fpo/supply/<sid>/allocation/add` *`/update`* `/remove` | Manage allocations |
| GET/POST `/fpo/supply/<sid>/edit` *`/delete`* | Edit/delete pool |
| POST `/fpo/quote/<rid>`, `/fpo/quote/<qid>/cancel` | Quote or withdraw |
| GET `/api/analytics/fpo/<fid>` | FPO analytics |

### Bulk buyer
| Method & Path | Purpose |
|---|---|
| GET `/bulk-buyer/dashboard` | Tabs: find, requests, quotes, orders, create |
| POST `/bulk-requirement`, `/bulk-requirement/<rid>/cancel` | Publish/cancel RFQ |
| POST `/bulk/quote/<qid>/accept\|reject` | Respond to quotes |
| POST `/bulk-order/<oid>/complete\|cancel` | Run bulk order lifecycle |

### Logistics & admin
| Method & Path | Purpose |
|---|---|
| GET `/logistics/dashboard` | Stats, assignments, vehicles, notifications |
| GET/POST `/logistics/vehicle/new` *(edit/delete)* | Vehicle CRUD |
| POST `/logistics/assignment/<aid>/update` | Advance delivery status pipeline |
| GET `/logistics/assignment/<aid>` | Assignment detail |
| POST `/api/logistics/assign` | **Admin auto-assign** delivery to a verified partner |
| POST `/api/route/optimize` | Route solver |
| GET `/route-optimizer` | Route planner UI |
| GET `/admin` | Admin panel / moderation |
| POST `/admin/product/<pid>/delete` | Delete a product |
| POST `/admin/<kind>/<uid>/<verb>` | Verify/reject FPO & bulk partners |
| GET `/admin/logistics` | All deliveries + logistics partners |
| POST `/admin/logistics/logistics/<uid>/verify\|reject` | Verify logistics partner |
| GET `/admin/intelligence`, `/api/demand-forecast[/<name>]`, `/api/demand-analytics`, `/admin/demand` | Analytics |
| GET `/map`, `/api/map/data` | Supply-chain map |
| GET `/api/alerts` | Alert feed |

---

## 11. Core Workflows (end-to-end)

### Retail order (customer buys from farmer/FPO)
1. Customer browses `/products`, opens a product, POSTs to `/cart/add/<pid>`.
2. Cart → `/checkout` → `/place-order` (COD, or Razorpay online).
3. Order created (`status=Placed`), stock decremented, farmer notified.
4. Farmer sees the order in `/orders` + dashboard and advances status
   (`Confirmed → Out for Delivery → Delivered`), OTP-confirmed on delivery.
5. Customer tracks progress live on `/track?order=N` and can chat with the farmer.

### FPO aggregate supply
1. FPO adds member farmers; members list their own products.
2. FPO creates a **pool** (`is_aggregate=1`) and adds allocations
   (`committed_qty` per member product). Pool stock = sum of `min(committed, stock)`.
3. Customers/bulk buyers buy the pool; `supply_consumption` records exactly how
   much came from which member (and reduces each member's stock/commitment).

### Bulk RFQ loop (bulk buyer ↔ FPO)
1. Bulk buyer posts a requirement (`Open`).
2. FPO sees matching open requirements (token-based match on unit + category +
   title); quotes with `price`, `quantity` ≤ available pool stock.
3. Buyer accepts → `bulk_quotes` → `Accepted`, requirement → `Awarded`,
   **bulk_order** → `Active`, stock consumed.
4. Buyer completes → order `Completed`, requirement `Closed`. FPO sees it too.

### Delivery & logistics
1. Admin can inspect all active deliveries at `/admin/logistics`.
2. Registration verifies logistics partners (admin action).
3. `POST /api/logistics/assign` auto-assigns the best **verified** partner with an
   **available vehicle** whose capacity ≥ required.
4. Logistics partner walks the assignment `Assigned → Pickup Pending → Picked Up
   → In Transit → Arrived → Delivered`; delivery events generate notifications.

---

## 12. Services (Business Modules)

- **`services/logistics.py`** — vehicle CRUD, assignment creation, status
  validation, `auto_assign_delivery` (nearest verified partner with capacity).
- **`services/notifications.py`** — `notify_user`, order/quote/delivery events.
- **`services/demand_forecast.py`** — scikit-learn forecasts + caching + admin analytics.
- **`services/matching.py`** — RFQ ↔ supply matching helpers.
- **`services/pricing.py`** — platform fee percentage, per-item price breakdowns.
- **`services/route_optimizer.py`** — pickup/delivery route optimisation.
- **`services/location.py`** — Haversine distance.
- **`services/alerts.py`** — rule-based shortage alerts.
- **`services/analytics.py`** — aggregated feed used by API endpoints.

---

## 13. Testing

Repos contains standalone test scripts (each prints a pass/fail verdict):

| Script | Coverage |
|---|---|
| `smoke_test.py` | End-to-end retail flow |
| `sec_test.py` | Access control / order visibility |
| `feature_test.py` | OTP, chat, dashboard |
| `bulk_flow_test.py` | FPO + bulk buyer RFQ loop (uses a throwaway DB) |
| `bulk_supply_flow_test.py`, `supply_flow_test.py` | Aggregate supply flows |
| `test_route_step5.py` | Route optimizer |
| `test_price_transparency.py` | Fee breakdowns |
| `test_notifications.py` | Notification engine |

Run: `python smoke_test.py` etc. (each expects `ALL PASS`).

---

## 14. Security Notes

- Passwords hashed with PBKDF2; sessions signed — never store plaintext.
- `role_required` + order-access checks (`_can_access_order`) prevent cross-user
  data leaks.
- Delivery OTP required before order can be marked Delivered.
- Open-redirect protection on login `next`.
- Image uploads limited by extension (`.jpg/.jpeg/.png/.webp`) and ≤ 5 MB.
- SQL is parameterised throughout (`?` placeholders).
- Service-layer fixes worth remembering:
  - `services.logistics.py` auto-assign must select `lp.city` and index rows
    with `[]` (sqlite3.Row has no `.get()`).

---

## 15. Deployment (Render / PythonAnywhere)

Guides live in `DEPLOY.md`. Briefly:

- **Render.com** — uses `render.yaml`; web service command
  `waitress-serve --port=10000 app:app`. Free tier resets the filesystem on
  redeploys, so the SQLite DB is ephemeral there.
- **PythonAnywhere** — free tier keeps files persistent; run via WSGI pointing
  at `app:app`.

Before going live:

- [ ] Set a strong `SECRET_KEY`.
- [ ] Add `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` if accepting online payments.
- [ ] Create an admin (`python make_admin.py your@email.com`).

---

## 16. Common Sandbox Accounts (password `123456`)

- Farmer: `y@g.com`, `h@g.com`
- Customer: `s@g.com`, `guest@g.com`
- FPO: `f@g.com`
- Bulk buyer: `b@g.com`
- Logistics: `logi@g.com`
- Admin: `admin@farmlink.com` (password `password123`)

---

## 17. FAQ / Troubleshooting

- **"Online payment isn't showing"** → Razorpay keys missing; checkout falls
  back to COD by design.
- **"Auto-assign failed"** → need at least one **verified** logistics partner
  with an **available** vehicle whose `capacity_kg` covers the requirement.
- **"FPO pool has 0 stock"** → no allocations yet; add member allocations on
  the supply detail page.
- **"Duplicate Basmati Rice listings"** → products are per-user; the sandbox DB
  accumulated duplicates from testing. Delete extras from the seller account.
- **"Database not resetting"** → `seed.py` skips when any user exists; delete
  `farmmarket.db` to reseed.
- **"Track order not visible"** → order access is restricted to the buyer,
  involved farmer/seller, or admin.