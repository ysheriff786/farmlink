import sys

from werkzeug.security import generate_password_hash

from app import app
from models import User, db


def main():
    if len(sys.argv) != 2:
        print("Usage: py make_admin.py <email>")
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name="Admin", email=email,
                        password_hash=generate_password_hash("password123"), role="admin")
            db.session.add(user)
            print(f"Created admin user {email} with password 'password123' - change it after first login!")
        else:
            user.role = "admin"
            print(f"{email} is now an admin.")
        db.session.commit()


if __name__ == "__main__":
    main()
