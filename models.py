from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.Text, nullable=False)
    email = db.Column(db.Text, unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(
        db.Text,
        db.CheckConstraint(
            "role IN ('farmer','fpo','customer','bulk_buyer','admin','logistics')"
        ),
        nullable=False,
    )
   
    
    phone = db.Column(db.Text, default="")
    address = db.Column(db.Text, default="")
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    city = db.Column(db.Text, default="")
    state = db.Column(db.Text, default="")
    pincode = db.Column(db.Text, default="")
    is_verified = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )

    products = db.relationship("Product", backref="farmer", lazy="dynamic")
    fpo_profile = db.relationship(
        "FPOProfile", backref="user", uselist=False, lazy="joined"
    )
    bulk_profile = db.relationship(
        "BulkBuyerProfile", backref="user", uselist=False, lazy="joined"
    )
    logistics_profile = db.relationship(
        "LogisticsProfile", backref="user", uselist=False, lazy="joined"
    )


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    farmer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, nullable=False)
    mrp = db.Column(db.Float)
    category = db.Column(db.Text, default="Vegetables")
    unit = db.Column(db.Text, default="kg")
    stock = db.Column(db.Integer, default=0)
    image = db.Column(db.Text, default="")
    is_aggregate = db.Column(
        db.Integer, db.CheckConstraint("is_aggregate IN (0,1)"),
        nullable=False, default=0,
    )
    min_qty = db.Column(db.Float, default=1)
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class CartItem(db.Model):
    __tablename__ = "cart_items"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    quantity = db.Column(db.Integer, nullable=False, default=1)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    total = db.Column(db.Float, nullable=False)
    status = db.Column(db.Text, default="Placed")
    payment_method = db.Column(db.Text, default="COD")
    payment_id = db.Column(db.Text, default="")
    rzp_order_id = db.Column(db.Text, default="")
    delivery_otp = db.Column(db.Text, default="")
    address = db.Column(db.Text, default="")
    phone = db.Column(db.Text, default="")
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id"), nullable=False
    )
    product_name = db.Column(db.Text, nullable=False)
    farmer_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    rating = db.Column(
        db.Integer,
        db.CheckConstraint("rating BETWEEN 1 AND 5"),
        nullable=False,
    )
    comment = db.Column(db.Text, default="")
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )

    __table_args__ = (db.UniqueConstraint("product_id", "user_id"),)


class Subscriber(db.Model):
    __tablename__ = "subscribers"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.Text, unique=True, nullable=False)
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id"), nullable=False
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class FPOProfile(db.Model):
    __tablename__ = "fpo_profiles"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), primary_key=True
    )
    registration_id = db.Column(db.Text, default="")
    description = db.Column(db.Text, default="")
    contact_person = db.Column(db.Text, default="")
    phone = db.Column(db.Text, default="")
    email = db.Column(db.Text, default="")
    address = db.Column(db.Text, default="")
    city = db.Column(db.Text, default="")
    state = db.Column(db.Text, default="")
    pincode = db.Column(db.Text, default="")
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    member_count = db.Column(db.Integer, default=0)
    main_crops = db.Column(db.Text, default="")
    verification_status = db.Column(
        db.Text,
        db.CheckConstraint(
            "verification_status IN ('pending','verified','rejected')"
        ),
        default="pending",
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class BulkBuyerProfile(db.Model):
    __tablename__ = "bulk_buyer_profiles"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), primary_key=True
    )
    buyer_type = db.Column(
        db.Text,
        db.CheckConstraint(
            "buyer_type IN ('Hotel','Restaurant','Retailer','Supermarket',"
            "'Food Processor','Institution','Other')"
        ),
        default="Other",
    )
    contact_person = db.Column(db.Text, default="")
    phone = db.Column(db.Text, default="")
    email = db.Column(db.Text, default="")
    address = db.Column(db.Text, default="")
    city = db.Column(db.Text, default="")
    state = db.Column(db.Text, default="")
    pincode = db.Column(db.Text, default="")
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    verification_status = db.Column(
        db.Text,
        db.CheckConstraint(
            "verification_status IN ('pending','verified','rejected')"
        ),
        default="pending",
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class FPOMember(db.Model):
    __tablename__ = "fpo_members"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fpo_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    farmer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    status = db.Column(
        db.Text,
        db.CheckConstraint("status IN ('pending','approved')"),
        default="pending",
    )
    joined_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )

    __table_args__ = (db.UniqueConstraint("fpo_id", "farmer_id"),)


class BulkRequirement(db.Model):
    __tablename__ = "bulk_requirements"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    buyer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.Text, default="Vegetables")
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit = db.Column(db.Text, default="kg")
    target_price = db.Column(db.Float)
    city = db.Column(db.Text, default="")
    pincode = db.Column(db.Text, default="")
    status = db.Column(
        db.Text,
        db.CheckConstraint(
            "status IN ('Open','Awarded','Closed','Cancelled')"
        ),
        default="Open",
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class BulkQuote(db.Model):
    __tablename__ = "bulk_quotes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    requirement_id = db.Column(
        db.Integer, db.ForeignKey("bulk_requirements.id"), nullable=False
    )
    fpo_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1)
    notes = db.Column(db.Text, default="")
    supply_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    status = db.Column(
        db.Text,
        db.CheckConstraint(
            "status IN ('Pending','Accepted','Rejected')"
        ),
        default="Pending",
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class BulkOrder(db.Model):
    __tablename__ = "bulk_orders"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    requirement_id = db.Column(
        db.Integer, db.ForeignKey("bulk_requirements.id"), nullable=False
    )
    quote_id = db.Column(
        db.Integer, db.ForeignKey("bulk_quotes.id"), nullable=False
    )
    buyer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    fpo_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    title = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    delivery_address = db.Column(db.Text, default="")
    supply_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    status = db.Column(
        db.Text,
        db.CheckConstraint(
            "status IN ('Active','Completed','Cancelled')"
        ),
        default="Active",
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class SupplyAllocation(db.Model):
    __tablename__ = "supply_allocations"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    supply_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    committed_qty = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )

    __table_args__ = (
        db.UniqueConstraint("supply_id", "product_id"),
    )


class SupplyConsumption(db.Model):
    __tablename__ = "supply_consumption"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    supply_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    order_item_id = db.Column(
        db.Integer, db.ForeignKey("order_items.id")
    )
    allocation_id = db.Column(
        db.Integer, db.ForeignKey("supply_allocations.id"), nullable=False
    )
    bulk_order_id = db.Column(
        db.Integer, db.ForeignKey("bulk_orders.id")
    )
    qty = db.Column(db.Float, nullable=False)
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class LogisticsProfile(db.Model):
    __tablename__ = "logistics_profiles"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), primary_key=True
    )
    company_name = db.Column(db.Text, default="")
    contact_person = db.Column(db.Text, default="")
    phone = db.Column(db.Text, default="")
    email = db.Column(db.Text, default="")
    address = db.Column(db.Text, default="")
    city = db.Column(db.Text, default="")
    state = db.Column(db.Text, default="")
    pincode = db.Column(db.Text, default="")
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    license_number = db.Column(db.Text, default="")
    fleet_size = db.Column(db.Integer, default=0)
    verification_status = db.Column(
        db.Text,
        db.CheckConstraint(
            "verification_status IN ('pending','verified','rejected')"
        ),
        default="pending",
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class Vehicle(db.Model):
    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    logistics_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    vehicle_type = db.Column(
        db.Text,
        db.CheckConstraint(
            "vehicle_type IN ('Bike','Auto','Van','Truck','Mini Truck',"
            "'Container','Other')"
        ),
        default="Truck",
    )
    registration_number = db.Column(db.Text, default="")
    driver_name = db.Column(db.Text, default="")
    driver_phone = db.Column(db.Text, default="")
    capacity_kg = db.Column(db.Float, nullable=False, default=0)
    capacity_volume = db.Column(db.Float, default=0)
    is_available = db.Column(
        db.Integer, db.CheckConstraint("is_available IN (0,1)"),
        nullable=False, default=1,
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class DeliveryAssignment(db.Model):
    __tablename__ = "delivery_assignments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    bulk_order_id = db.Column(db.Integer, db.ForeignKey("bulk_orders.id"))
    logistics_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"))
    pickup_address = db.Column(db.Text, default="")
    pickup_city = db.Column(db.Text, default="")
    pickup_latitude = db.Column(db.Float)
    pickup_longitude = db.Column(db.Float)
    destination_address = db.Column(db.Text, default="")
    destination_city = db.Column(db.Text, default="")
    destination_latitude = db.Column(db.Float)
    destination_longitude = db.Column(db.Float)
    required_capacity_kg = db.Column(db.Float, default=0)
    status = db.Column(
        db.Text,
        db.CheckConstraint(
            "status IN ('Assigned','Pickup Pending','Picked Up','In Transit',"
            "'Arrived','Delivered','Failed','Cancelled')"
        ),
        default="Assigned",
    )
    notes = db.Column(db.Text, default="")
    assigned_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )
    picked_up_at = db.Column(db.Text)
    delivered_at = db.Column(db.Text)
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    type = db.Column(
        db.Text,
        db.CheckConstraint(
            "type IN ('info','order','delivery','quote','alert','system')"
        ),
        default="info",
    )
    title = db.Column(db.Text, nullable=False)
    message = db.Column(db.Text, default="")
    reference_type = db.Column(db.Text, default="")
    reference_id = db.Column(db.Integer)
    is_read = db.Column(
        db.Integer,
        db.CheckConstraint("is_read IN (0,1)"),
        nullable=False, default=0,
    )
    created_at = db.Column(
        db.Text, default=lambda: datetime.utcnow().isoformat()
    )
