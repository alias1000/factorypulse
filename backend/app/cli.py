"""Operations CLI.

    python -m app.cli create-user --email admin@firma.de --role admin [--name "..."]   (password prompted)
    python -m app.cli reset-password --email user@firma.de                           (password prompted)
    python -m app.cli list-users
    python -m app.cli backup [--note "..."]
    python -m app.cli list-backups
    python -m app.cli restore <backup-name-or-directory> [--yes] [--no-safety-backup]
    python -m app.cli check                                                          (readiness checks)

Passwords can also be piped via --password-stdin (for automation); they are never accepted as arguments.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from sqlalchemy import select

from .db import SessionLocal, engine
from .models import User
from .services import auth as A
from .services import backup as B
from .services.data_access import get_company
from .services.settings_store import audit


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match")
    return pw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="")
    c.add_argument("--role", choices=list(A.ROLE_ORDER), default="viewer")
    c.add_argument("--password-stdin", action="store_true")
    c.add_argument("--no-force-change", action="store_true", help="do not require a password change at first login")
    r = sub.add_parser("reset-password")
    r.add_argument("--email", required=True)
    r.add_argument("--password-stdin", action="store_true")
    sub.add_parser("list-users")
    b = sub.add_parser("backup")
    b.add_argument("--note")
    b.add_argument("--kind", choices=["manual", "scheduled"], default="manual")
    sub.add_parser("list-backups")
    rs = sub.add_parser("restore")
    rs.add_argument("backup", help="backup name in the backup directory, or path to an extracted backup directory")
    rs.add_argument("--yes", action="store_true")
    rs.add_argument("--no-safety-backup", action="store_true")
    sub.add_parser("check")
    args = ap.parse_args(argv)

    with SessionLocal() as db:
        if args.cmd == "create-user":
            company = get_company(db)
            try:
                u = A.create_user(db, company.id, args.email, args.name or args.email, args.role, _password(args),
                                  must_change_password=not args.no_force_change)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            audit(db, "user.created", company.id, "user", u.id, {"email": u.email, "role": u.role, "via": "cli"})
            db.commit()
            print(f"created {u.role} {u.email}")
        elif args.cmd == "reset-password":
            u = db.scalar(select(User).where(User.email == A.normalize_email(args.email)))
            if not u:
                print("error: user not found", file=sys.stderr)
                return 1
            pw = _password(args)
            problems = A.password_problems(pw, u.email)
            if problems:
                print("error: " + " ".join(problems), file=sys.stderr)
                return 1
            u.password_hash, u.must_change_password, u.locked_until, u.failed_login_count = A.hash_password(pw), True, None, 0
            A.revoke_all_sessions(db, u.id)
            audit(db, "user.password_reset", u.company_id, "user", u.id, {"via": "cli"})
            db.commit()
            print(f"password reset for {u.email} (must be changed at next login)")
        elif args.cmd == "list-users":
            for u in db.scalars(select(User).order_by(User.email)):
                print(f"{u.email:40} {u.role:8} {'active' if u.is_active else 'inactive'}")
        elif args.cmd == "backup":
            man = B.create_backup(engine, "cli", args.kind, args.note)
            print(json.dumps(man, indent=2))
        elif args.cmd == "list-backups":
            for m in B.list_backups():
                print(f"{m['name']}  {m['created_at']}  {m['kind']:11} rev={m['alembic_revision']}  {m['total_size'] / 1e6:.1f} MB")
        elif args.cmd == "restore":
            target = Path(args.backup) if "/" in args.backup else args.backup
            if not args.yes and input(f"Restore {args.backup}? ALL current data will be replaced. Type RESTORE: ") != "RESTORE":
                print("aborted")
                return 1
            db.close()
            result = B.restore_backup(engine, target, "cli", safety_backup=not args.no_safety_backup)
            with SessionLocal() as db2:
                audit(db2, "backup.restored", get_company(db2).id, details={**result, "by": "cli"})
                db2.commit()
            print(json.dumps(result, indent=2))
        elif args.cmd == "check":
            from .api.system import run_checks
            checks = run_checks(db)
            for ch in checks:
                print(f"[{ch['status'].upper():4}] {ch['name']:18} {ch['detail']}")
            return 0 if all(ch["status"] != "fail" for ch in checks) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
