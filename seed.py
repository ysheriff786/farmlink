from werkzeug.security import generate_password_hash

from app import app
from models import (
    BulkBuyerProfile,
    BulkRequirement,
    FPOMember,
    FPOProfile,
    Product,
    SupplyAllocation,
    User,
    db,
)

DEMO_USERS = [
    ("Ramesh Kumar", "ramesh@farm.com", "farmer", "9876500001"),
    ("Lakshmi Devi", "lakshmi@farm.com", "farmer", "9876500002"),
    ("Anita Sharma", "anita@mail.com", "customer", "9876500003"),
    ("Vijay Patel", "vijay@mail.com", "customer", "9876500004"),
    ("FarmLink Admin", "admin@farmlink.com", "admin", ""),
    ("Green Soil FPO", "green@fpo.com", "fpo", "9876500005"),
    ("Sunrise Hotels", "sunrise@buyer.com", "bulk_buyer", "9876500006"),
]

DEMO_ADDRESSES = {
    "anita@mail.com": "12/4 Rose Garden Street, Gandhi Nagar, Coimbatore 641012",
    "vijay@mail.com": "Plot 27, Shivaji Nagar, Near Bus Stand, Nashik 422001",
}

DEMO_PRODUCTS = [
    ("Ramesh Kumar", "Organic Tomatoes", "Juicy farm-fresh tomatoes grown without chemicals.", 40.0, "kg", 120),
    ("Ramesh Kumar", "Fresh Spinach", "Tender green spinach leaves picked this morning.", 25.0, "bunch", 60),
    ("Ramesh Kumar", "Country Eggs", "Free-range eggs from hens raised on open pasture.", 90.0, "dozen", 30),
    ("Lakshmi Devi", "Alphonso Mangoes", "Sweet aromatic mangoes from our family orchard.", 220.0, "kg", 45),
    ("Lakshmi Devi", "Raw Forest Honey", "Pure unprocessed honey collected from wild hives.", 350.0, "pack", 20),
    ("Lakshmi Devi", "Desi Cow Milk", "Fresh whole milk from grass-fed desi cows.", 60.0, "litre", 80),
    ("Green Soil FPO", "FPO Onions (New Crop)", "Bulk supply of cleaned red onions from FPO producer members.", 28.0, "kg", 1500),
    ("Green Soil FPO", "FPO Tomatoes (Farm Tray)", "Sorted, graded tomatoes for bulk buyers.", 32.0, "kg", 900),
]

DEMO_REQUIREMENTS = [
    ("sunrise@buyer.com", "300 kg onions weekly", "Steady weekly supply of red onions for hotel kitchen.",
     "Vegetables", 300, "kg", 26.0, "Bengaluru", "560078"),
    ("sunrise@buyer.com", "150 kg fresh tomato for festival menu", "Farm-fresh tomatoes needed for curries and salads.",
     "Vegetables", 150, "kg", 34.0, "Bengaluru", "560078"),
]


with app.app_context():
    if User.query.count() > 0:
        print("Database already has data. Skipping seed.")
    else:
        users = {}
        for name, email, role, phone in DEMO_USERS:
            u = User(name=name, email=email,
                     password_hash=generate_password_hash("password123"),
                     role=role, phone=phone,
                     is_verified=role not in ("fpo", "bulk_buyer"))
            db.session.add(u)
            db.session.flush()
            users[email] = u.id
        for email, address in DEMO_ADDRESSES.items():
            User.query.filter_by(email=email).first().address = address
        for farmer_email, pname, desc, price, unit, stock in [
                ("ramesh@farm.com" if f == "Ramesh Kumar"
                 else "lakshmi@farm.com" if f == "Lakshmi Devi"
                 else "green@fpo.com",
                 p, d, pr, u, s)
                for f, p, d, pr, u, s in DEMO_PRODUCTS]:
            db.session.add(Product(farmer_id=users[farmer_email], name=pname,
                                   description=desc, price=price, unit=unit, stock=stock))
        green_id = users["green@fpo.com"]
        db.session.add(FPOProfile(
            user_id=green_id, registration_id="FPO/KA/2019/448",
            description="Farmer producer organisation from Mandya district.",
            contact_person="Suresh Gowda", phone="9876500005", email="green@fpo.com",
            address="KRS Road, Srirangapatna", city="Mandya", state="Karnataka",
            pincode="571438", member_count=320, main_crops="Tomatoes, Onion, Rice, Bananas",
            verification_status="verified"))
        db.session.add(BulkBuyerProfile(
            user_id=users["sunrise@buyer.com"], buyer_type="Hotel",
            contact_person="Nithya Rao", phone="9876500006", email="sunrise@buyer.com",
            address="Hosur Road, JP Nagar", city="Bengaluru", state="Karnataka",
            pincode="560078", verification_status="verified"))
        for member_email in ("ramesh@farm.com", "lakshmi@farm.com"):
            db.session.add(FPOMember(fpo_id=green_id, farmer_id=users[member_email], status="approved"))
        db.session.flush()
        tomato = Product.query.filter_by(farmer_id=users["ramesh@farm.com"], name="Organic Tomatoes").first()
        committed = min(100, tomato.stock)
        pool = Product(farmer_id=green_id, name="FPO Red Tomato Pool (Aggregate)",
                       description="Farm-gate tomatoes pooled from Green Soil FPO member farmers.",
                       price=38.0, category="Vegetables", unit="kg", stock=committed,
                       is_aggregate=1, min_qty=5)
        db.session.add(pool)
        db.session.flush()
        db.session.add(SupplyAllocation(supply_id=pool.id, product_id=tomato.id, committed_qty=committed))
        for buyer_email, title, desc, category, qty, unit, target, city, pincode in DEMO_REQUIREMENTS:
            db.session.add(BulkRequirement(buyer_id=users[buyer_email], title=title,
                                           description=desc, category=category, quantity=qty,
                                           unit=unit, target_price=target, city=city, pincode=pincode))
        db.session.commit()
        print("Seeded 7 users, 9 products, 2 org profiles, 2 members, 1 aggregate supply "
              "and 2 bulk requirements.")
        print("Password for all demo accounts: password123")
        print("Admin login: admin@farmlink.com")
        print("FPO login: green@fpo.com | Bulk buyer login: sunrise@buyer.com")
