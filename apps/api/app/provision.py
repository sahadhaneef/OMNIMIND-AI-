"""Interactive provisioning: credentials never appear in command-line arguments."""

import argparse
import getpass
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hasher
from app.db.models import Membership, Organization, Role, User
from app.db.session import get_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=[r.value for r in Role], default=Role.ADMIN.value)
    args = parser.parse_args()
    email = args.email.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug) or len(args.slug) > 80:
        parser.error("Use a lowercase hyphenated slug, maximum 80 characters")
    if not 1 <= len(args.organization.strip()) <= 120 or "@" not in email or len(email) > 254:
        parser.error("Invalid organization name or email")
    with Session(get_engine()) as db:
        # Serialize provisioning of the same slug, including first creation.
        from sqlalchemy import text

        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:slug, 0))"), {"slug": args.slug}
        )
        organization = db.scalar(select(Organization).where(Organization.slug == args.slug))
        if not organization:
            if args.role != Role.ADMIN:
                parser.error("A new organization requires an administrator")
            organization = Organization(name=args.organization.strip(), slug=args.slug)
            db.add(organization)
            db.flush()
        user = db.scalar(select(User).where(User.email == email))
        if organization.status != "active" or (user and user.status != "active"):
            parser.error("Cannot provision a disabled organization or user")
        if not user:
            password = getpass.getpass("New user password (minimum 12 characters): ")
            if len(password) < 12 or len(password) > 1024:
                parser.error("Password must contain 12 to 1024 characters")
            if password != getpass.getpass("Confirm password: "):
                parser.error("Passwords do not match")
            user = User(email=email, password_hash=hasher.hash(password))
            db.add(user)
            db.flush()
        membership = db.scalar(
            select(Membership).where(
                Membership.organization_id == organization.id, Membership.user_id == user.id
            )
        )
        if membership:
            print("Membership already exists; credentials and role unchanged.")
        else:
            db.add(Membership(organization_id=organization.id, user_id=user.id, role=args.role))
        db.commit()
        print(f"Provisioned user {user.id} in organization {organization.id}")


if __name__ == "__main__":
    main()
