"""Administration from the trusted server terminal, without public role escalation."""
import argparse
import getpass
from sqlalchemy import select
from server.core.config import settings
from server.core.security import hash_password
from server.database.base import init_db, seed_db
from server.database.session import SessionLocal
from server.models.users import User, ROLES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create-admin", "set-role", "seed-demo"])
    parser.add_argument("--email")
    parser.add_argument("--name", default="Administrator")
    parser.add_argument("--role", choices=ROLES, default="it_admin")
    args = parser.parse_args()
    init_db()
    if args.command == "seed-demo":
        if settings.ENVIRONMENT == "production":
            parser.error("Demo accounts are disabled in production")
        seed_db()
        return
    if not args.email or "@" not in args.email:
        parser.error("--email is required")
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
        if args.command == "create-admin":
            if user:
                parser.error("Account already exists; use set-role")
            password = getpass.getpass("Password (at least 10 characters): ")
            if len(password) < 10 or password != getpass.getpass("Confirm password: "):
                parser.error("Password too short or confirmation differs")
            db.add(User(full_name=args.name, email=args.email.strip().lower(), role=args.role, password_hash=hash_password(password), is_verified=True))
        else:
            if not user:
                parser.error("Account does not exist")
            user.role = args.role
    print("Account updated")


if __name__ == "__main__":
    main()
