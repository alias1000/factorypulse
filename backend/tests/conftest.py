"""Test configuration.

Uses the database in FP_TEST_DATABASE_URL (PostgreSQL recommended, e.g.
postgresql+psycopg://factorypulse:devpass@localhost:5432/factorypulse_test).
Falls back to a temporary SQLite file so the KPI/unit tests run anywhere.
The schema is created through the Alembic migrations, so migrations are tested too.
"""
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="fp-test-"))
os.environ["FP_DATABASE_URL"] = os.environ.get("FP_TEST_DATABASE_URL", f"sqlite:///{_tmp/'test.db'}")
os.environ["FP_DATA_DIR"] = str(_tmp / "var")
os.environ["FP_SEED_DEMO_ON_STARTUP"] = "false"
os.environ["FP_ENVIRONMENT"] = "test"
os.environ.pop("FP_INITIAL_ADMIN_EMAIL", None)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    from app.db import engine

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    yield


TEST_PASSWORD = "Test-Passwort-2026!"
ROLES = ("admin", "analyst", "viewer")


def email_for(role: str) -> str:
    return f"{role}@test.example"


@pytest.fixture(scope="session")
def app(_migrated_db):
    from app.db import SessionLocal
    from app.main import app as fastapi_app
    from app.services import auth as A
    from app.services.data_access import get_company

    with SessionLocal() as db:
        company = get_company(db)
        for role in ROLES:
            A.create_user(db, company.id, email_for(role), role.title(), role, TEST_PASSWORD, must_change_password=False)
        db.commit()
    return fastapi_app


def login(app, role: str | None = "admin") -> TestClient:
    """Return a TestClient logged in as ``role`` with the CSRF header preset (None = anonymous)."""
    from app.services.auth import login_limiter

    login_limiter.reset()
    c = TestClient(app)
    c.__enter__()
    if role:
        r = c.post("/api/auth/login", json={"email": email_for(role), "password": TEST_PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


@pytest.fixture(scope="session")
def client(app):
    c = login(app, "admin")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture()
def as_role(app):
    clients = []

    def make(role):
        c = login(app, role)
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture()
def demo(client):
    """Reset the database to the synthetic demo dataset."""
    r = client.post("/api/admin/demo/load", json={"password": TEST_PASSWORD, "confirm": "RELOAD"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def frozen_clock(client):
    """Freeze the application clock (app/clock.py). Use ``frozen_clock.set(...)`` / ``.advance(...)``.
    Log in with ``as_role`` *inside* the test so sessions are created at the frozen time.

    Moving the clock far ahead lets the server purge/expire sessions created in real time (correct
    behaviour), so the shared admin ``client`` signs in again afterwards."""
    import datetime as dt

    from app import clock
    from app.services.auth import login_limiter

    with clock.freeze(dt.datetime(2026, 10, 17, 10, 0, tzinfo=dt.timezone.utc)) as fc:
        yield fc
    login_limiter.reset()
    r = client.post("/api/auth/login", json={"email": email_for("admin"), "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]


FAKE_SCANNER = r'''
import sys
data = open(sys.argv[-1], "rb").read()
if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
    print("stream: Eicar-Test-Signature FOUND"); sys.exit(1)
if b"SCANNER-CRASH" in data:
    print("scanner internal error", file=sys.stderr); sys.exit(2)
print("stream: OK"); sys.exit(0)
'''


@pytest.fixture(autouse=True)
def _reset_scanner_health(_migrated_db):
    """v0.3.3: the persisted scanner health depends on the scanner configuration of each test (fake
    scanners, ports); start every test without health state so tests stay order-independent."""
    yield
    from app.db import SessionLocal
    from app.models import ScannerHealth

    with SessionLocal() as db:
        db.query(ScannerHealth).delete()
        db.commit()


@pytest.fixture()
def fake_scanner(monkeypatch, tmp_path):
    """Configure the 'command' malware-scanner integration with a deterministic fake scanner
    (EICAR test string = infected, SCANNER-CRASH = scanner error, anything else = clean)."""
    import sys

    from app.config import get_settings

    script = tmp_path / "fake_scanner.py"
    script.write_text(FAKE_SCANNER)
    s = get_settings()
    monkeypatch.setattr(s, "evidence_scanner", "command")
    monkeypatch.setattr(s, "evidence_scan_command", f"{sys.executable} {script}")
    return s


@pytest.fixture()
def analyst_id(client):
    return next(u["id"] for u in client.get("/api/action-center/assignees").json() if u["email"] == email_for("analyst"))


@pytest.fixture()
def master_data(client):
    """Production lines/machines must exist (tests must not depend on an earlier test having loaded
    them). Loads the synthetic demo dataset only if the test database has no master data yet."""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import ProductionLine

    with SessionLocal() as db:
        n = db.scalar(select(func.count(ProductionLine.id)))
    if not n:
        r = client.post("/api/admin/demo/load", json={"password": TEST_PASSWORD, "confirm": "RELOAD"})
        assert r.status_code == 200, r.text
