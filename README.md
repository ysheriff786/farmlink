# FarmLink 🌾     render link: https://farmlink-kc05.onrender.com

An online marketplace that connects local farmers directly with customers. Farmers list their fresh produce, and customers order it at fair prices — no middlemen. Built with **Python (Flask)** and **SQLite**.

## Features

- **Farmer dashboard** — sales stats, 14-day earnings chart, top products, recent orders
- **Product management** — farmers create/edit/delete products with images, MRP, stock, categories
- **Shopping cart & checkout** — stock-aware cart, COD or online payment
- **Online payments** — Razorpay integration (works without it — falls back to COD)
- **Order tracking** — live status timeline with auto-refresh polling
- **Delivery OTP** — 6-digit code confirmed by the customer before marking Delivered
- **Order chat** — buyer ↔ farmer messaging per order
- **Reviews & ratings** — one review per product per user, editable
- **Roles** — customer, farmer, admin (admin panel with stats and moderation)
- **Extras** — dark mode, live navbar badges, search + category filters, newsletter, policy pages

## Tech Stack

| Layer    | Choice |
|----------|--------|
| Backend  | Python / Flask |
| Database | SQLite (no setup needed) |
| Frontend | Jinja2 templates + vanilla CSS/JS |
| Payments | Razorpay (optional) |

## Quick Start

```bash
cd farm-market
pip install -r requirements.txt
python app.py
```

### Optional: demo data

```bash
python seed.py
```

### Optional: online payments

Copy `.env.example` to `.env` and add your keys:

```
SECRET_KEY=change-this-to-a-long-random-string
RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx
```

Without keys the app still works — checkout simply offers Cash on Delivery only.

### Make yourself admin

```bash
python make_admin.py your@email.com
```

## Project Structure

```
farm-market/
├── app.py              # All routes, models, logic
├── seed.py             # Demo data seeder
├── make_admin.py       # Promote a user to admin
├── requirements.txt
├── render.yaml         # Render.com deploy config
├── static/             # CSS, JS, uploaded images
├── templates/          # Jinja2 pages
├── smoke_test.py       # End-to-end flow test
├── sec_test.py         # Security / access-control test
└── feature_test.py     # OTP, chat, dashboard tests
```

## Testing

Run all three suites (each prints `ALL PASS` when green):

```bash
python smoke_test.py
python sec_test.py
python feature_test.py
```

## Deployment

Free hosting guides are in [DEPLOY.md](DEPLOY.md):

- **Render.com** (~5 min, uses `render.yaml`) — note the free tier resets files on redeploy
- **PythonAnywhere** — free tier keeps data persistent

Before going live:

- [ ] Set a strong `SECRET_KEY` env variable (never commit it)
- [ ] Add Razorpay keys if you want online payments
- [ ] Register an admin account

## Security Notes

- Passwords hashed with Werkzeug (`pbkdf2`), sessions signed with `SECRET_KEY`
- Login required for tracking; order access restricted to buyer, involved farmer, or admin
- Delivery OTP required to mark orders Delivered
- Open-redirect protection on login `next`, upload type/size validation
