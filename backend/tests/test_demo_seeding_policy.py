"""v0.3.1 regression: a NEW production database must never receive synthetic demo records unless
seeding was explicitly enabled. Every scenario starts the real application (migrations + FastAPI
lifespan) in a separate process against a brand-new database, so cached settings cannot leak."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings

BACKEND = Path(__file__).resolve().parents[1]

PROBE = r"""
import json
from alembic import command
from alembic.config import Config
cfg = Config("alembic.ini")
command.upgrade(cfg, "head")
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as c:
    c.get("/api/health")
with TestClient(app):          # second start (container restart) must not change anything either
    pass
from sqlalchemy import func, select
from app.db import SessionLocal
from app.models import Company, Investigation, ProductionRecord, User
with SessionLocal() as db:
    print("RESULT" + json.dumps({
        "records": db.scalar(select(func.count(ProductionRecord.id))),
        "investigations": db.scalar(select(func.count(Investigation.id))),
        "synthetic_companies": db.scalar(select(func.count(Company.id)).where(Company.is_synthetic.is_(True))),
        "synthetic_investigations": db.scalar(select(func.count(Investigation.id)).where(Investigation.is_synthetic.is_(True))),
        "demo_users": db.scalar(select(func.count(User.id)).where(User.email.like("%@demo.factorypulse.invalid"))),
        "admins": db.scalar(select(func.count(User.id)).where(User.role == "admin", ~User.email.like("%@demo.factorypulse.invalid"))),
    }))
"""


def _start_fresh(tmp_path: Path, **env: str) -> dict:
    db = tmp_path / "fresh.db"
    e = {k: v for k, v in os.environ.items() if not k.startswith("FP_")}
    e.update({"FP_DATABASE_URL": f"sqlite:///{db}", "FP_DATA_DIR": str(tmp_path / "var"), "FP_LOG_LEVEL": "WARNING"})
    e.update(env)
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BACKEND, env=e, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-3000:]
    line = next(l for l in out.stdout.splitlines() if l.startswith("RESULT"))
    return json.loads(line[len("RESULT"):]) | {"log": out.stderr}


@pytest.mark.parametrize("env", [
    {"FP_ENVIRONMENT": "production"},                                   # default production
    {"FP_ENVIRONMENT": "production", "FP_SEED_DEMO_ON_STARTUP": ""},    # docker compose passes unset vars as ""
    {"FP_ENVIRONMENT": "production", "FP_SEED_DEMO_ON_STARTUP": "false"},
    {"FP_ENVIRONMENT": "staging"},                                      # unknown environments are treated like production
], ids=["production-default", "production-blank", "production-false", "staging"])
def test_new_production_database_gets_no_synthetic_data(tmp_path, env):
    r = _start_fresh(tmp_path, **env)
    assert r["records"] == 0 and r["investigations"] == 0
    assert r["synthetic_companies"] == 0 and r["synthetic_investigations"] == 0 and r["demo_users"] == 0
    assert "Synthetic demo dataset loaded" not in r["log"]


def test_explicitly_configured_demo_environment_is_still_seeded(tmp_path):
    r = _start_fresh(tmp_path, FP_ENVIRONMENT="demo", FP_INITIAL_ADMIN_EMAIL="chef@demo-werk.example",
                     FP_INITIAL_ADMIN_PASSWORD="Demo-Startkennwort-2026!")
    assert r["records"] > 1000 and r["synthetic_companies"] == 1 and r["synthetic_investigations"] == 8
    # regression: the demo placeholder accounts must not block the bootstrap administrator
    assert r["demo_users"] == 3 and r["admins"] == 1


def test_explicit_opt_in_in_production_is_honoured_and_warned(tmp_path):
    r = _start_fresh(tmp_path, FP_ENVIRONMENT="production", FP_SEED_DEMO_ON_STARTUP="true")
    assert r["records"] > 1000 and r["synthetic_companies"] == 1
    assert "FP_SEED_DEMO_ON_STARTUP=true in production" in r["log"]


def test_seeding_policy_matrix(monkeypatch):
    for k in ("FP_SEED_DEMO_ON_STARTUP", "FP_ENABLE_DEMO_TOOLS", "FP_ENVIRONMENT"):
        monkeypatch.delenv(k, raising=False)
    assert Settings(environment="production").demo_seeding_enabled is False
    assert Settings(environment="production", seed_demo_on_startup="").demo_seeding_enabled is False
    assert Settings(environment="test").demo_seeding_enabled is False
    assert Settings(environment="development").demo_seeding_enabled is True
    assert Settings(environment="demo").demo_seeding_enabled is True
    assert Settings(environment="demo", seed_demo_on_startup=False).demo_seeding_enabled is False
    assert Settings(environment="production", seed_demo_on_startup=True).demo_seeding_enabled is True
    # demo tools follow the same principle: off in production unless explicitly enabled
    assert Settings(environment="production").demo_tools_enabled is False
    assert Settings(environment="demo").demo_tools_enabled is True
