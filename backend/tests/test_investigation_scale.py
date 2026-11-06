"""v0.3.1 regression: investigation list filtering/sorting/pagination run in the database, scale to
50,000 investigations, and keep correct German Unicode search."""
import datetime as dt
import random
import time

import pytest
from sqlalchemy import delete, func, insert, select, text

from app.db import SessionLocal, engine
from app.models import Investigation, Machine, ProductionLine
from app.services.search import document, like_pattern, normalize

N = 50_000
TAG = "SCALE50K"


# ----------------------------------------------------------------------------- unit: normalisation

@pytest.mark.parametrize("text,term,hit", [
    ("Ölfilm am Boden", "ölfilm", True),
    ("Ölfilm am Boden", "ÖLFILM", True),
    ("Ölfilm am Boden", "Oelfilm", True),                 # transliteration variant
    ("Ölfilm am Boden", "Ölfilm", True),            # decomposed umlaut (macOS input)
    ("Druckluftstraße 3", "STRASSE", True),               # ß ↔ ss via case folding
    ("Druckluftstraße 3", "straße", True),
    ("Lüfter Überhitzung", "uberhitzung", False),         # "ü" is not "u"
    ("Lüfter Überhitzung", "ueberhitzung", True),
    ("Ausschuss 50% erhöht", "50%", True),
    ("Ausschuss 50 erhöht", "50%", False),                # % is literal, not a wildcard
    ("Linie_2 Störung", "e_2", True),
    ("Linie 2x Störung", "e_2", False),                   # _ is literal
])
def test_search_normalisation(text, term, hit):
    doc = document(text)
    needle = like_pattern(term)[1:-1].replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\")
    assert (needle in doc) is hit
    assert normalize("  A  B ") == "a b"


def test_api_search_german_text(client, demo):
    lines = client.get("/api/meta").json()["lines"]
    made = client.post("/api/investigations", json={
        "title": "Kühlmittelpumpe an der Prüfstraße überhitzt", "description": "Öl tritt aus; Maß 50% über Soll",
        "category": "equipment_failure", "severity": "low", "line_id": lines[0]["id"]}).json()
    for term in ("KÜHLMITTEL", "kuehlmittel", "prüfstrasse", "PRUEFSTRASSE", "Ü" "berhitzt", "50%", "maß", "MASS",
                 "Ölfilm", made["number"].lower()):
        rows = client.get("/api/investigations", params={"search": term, "page_size": 200}).json()["rows"]
        expect = term != "Ölfilm"
        assert (made["id"] in [r["id"] for r in rows]) is expect, term
    # the demo record with umlaut in the title is found case-insensitively
    assert client.get("/api/investigations", params={"search": "ÖLFILM"}).json()["total"] == 1
    # "%" is a literal character, not "match everything"
    all_n = client.get("/api/investigations").json()["total"]
    with SessionLocal() as db:
        with_pct = sum(1 for t in db.scalars(select(Investigation.search_text)) if "%" in (t or ""))
    assert client.get("/api/investigations", params={"search": "%"}).json()["total"] == with_pct < all_n


def test_search_text_is_maintained_on_update(client, demo):
    inv = client.get("/api/investigations", params={"search": "Ölfilm"}).json()["rows"][0]
    client.patch(f"/api/investigations/{inv['id']}", json={"title": "Hydrauliköl-Leckage an Prüfstand 12"}).raise_for_status()
    assert client.get("/api/investigations", params={"search": "hydraulikoel"}).json()["total"] == 1
    assert client.get("/api/investigations", params={"search": "Ölfilm am Boden"}).json()["total"] == 0


# ----------------------------------------------------------------------------- 50,000 investigations

@pytest.fixture(scope="module")
def big_dataset(client):
    """Bulk-insert 50,000 synthetic investigations (core INSERT – the ORM search_text hook is replicated)."""
    from app.services.data_access import get_company
    from conftest import TEST_PASSWORD

    client.post("/api/admin/demo/load", json={"password": TEST_PASSWORD, "confirm": "RELOAD"}).raise_for_status()

    rnd = random.Random(42)
    with SessionLocal() as db:
        company = get_company(db)
        lines = db.scalars(select(ProductionLine).where(ProductionLine.company_id == company.id)).all()
        if not lines:
            pytest.skip("needs master data (demo)")
        machines = db.scalars(select(Machine).where(Machine.company_id == company.id)).all()
        owners = [u for u in db.execute(text("SELECT id FROM users WHERE role IN ('admin','analyst')")).scalars()]
        words = ["Spindel", "Lager", "Ölfilm", "Kühlmittel", "Prüfstraße", "Überhitzung", "Ausschuss", "Stillstand",
                 "Förderband", "Drehmoment", "Schmierung", "Werkzeugbruch", "Maßabweichung", "Druckluft"]
        statuses = ["open", "in_progress", "pending_verification", "resolved", "closed", "reopened"]
        start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        rows = []
        for i in range(N):
            line = rnd.choice(lines)
            ms = [m for m in machines if m.line_id == line.id]
            title = f"{TAG} {rnd.choice(words)} {rnd.choice(words)} #{i}"
            desc = f"{rnd.choice(words)} an {line.code}; synthetic load-test record"
            created = start + dt.timedelta(minutes=rnd.randint(0, 60 * 24 * 900))
            st = rnd.choice(statuses)
            number = f"INV-2099-{i:05d}"
            rows.append({
                "company_id": company.id, "number": number, "title": title, "description": desc,
                "category": rnd.choice(["equipment_failure", "production_loss", "quality_issue", "other"]),
                "severity": rnd.choice(["low", "medium", "high", "critical"]), "status": st, "source": "manual",
                "line_id": line.id, "machine_id": rnd.choice(ms).id if ms and rnd.random() < .8 else None,
                "owner_id": rnd.choice(owners) if rnd.random() < .7 else None, "created_by_id": None,
                "due_date": (created + dt.timedelta(days=rnd.randint(1, 40))).date() if rnd.random() < .9 else None,
                "resolution_summary": None, "created_at": created, "updated_at": created, "status_changed_at": created,
                "resolved_at": created + dt.timedelta(hours=rnd.randint(1, 900)) if st in ("resolved", "closed") else None,
                "closed_at": None, "reopen_count": 0, "is_synthetic": True,
                "search_text": document(number, title, desc),
            })
        t = time.perf_counter()
        for k in range(0, N, 5000):
            db.execute(insert(Investigation), rows[k:k + 5000])
        db.commit()
        if engine.dialect.name == "postgresql":
            db.execute(text("ANALYZE investigations"))
            db.commit()
        print(f"\n  inserted {N} investigations in {time.perf_counter() - t:.1f}s")
    yield company.id
    with SessionLocal() as db:
        db.execute(delete(Investigation).where(Investigation.number.like("INV-2099-%")))
        db.commit()


def _timed(c, params):
    t = time.perf_counter()
    r = c.get("/api/investigations", params=params)
    ms = (time.perf_counter() - t) * 1000
    assert r.status_code == 200, r.text
    return r.json(), ms


def test_list_queries_at_50k(client, big_dataset):
    timings = {}
    cases = {
        "default page": {},
        "deep page": {"page": 1500, "page_size": 25},
        "search umlaut": {"search": "überhitzung"},
        "search transliterated": {"search": "ueberhitzung"},
        "search number": {"search": "INV-2099-04711"},
        "severity sort": {"sort": "severity", "order": "desc"},
        "due date asc": {"sort": "due_date", "order": "asc", "open_only": True},
        "filters": {"status": ["open", "reopened"], "severity": ["critical"], "unassigned": True},
        "overdue": {"overdue": True, "sort": "due_date"},
        "date range": {"start_date": "2025-03-29", "end_date": "2025-03-31"},
    }
    for name, params in cases.items():
        body, ms = _timed(client, {"page_size": 25, **params})
        timings[name] = (ms, body["total"])
    print("\n" + "\n".join(f"  {k:24s} {v[0]:7.1f} ms  total={v[1]}" for k, v in timings.items()))
    assert all(ms < 2000 for ms, _ in timings.values()), timings
    assert timings["search number"][1] == 1
    assert timings["search umlaut"][1] == timings["search transliterated"][1] > 1000
    assert timings["default page"][1] >= N


def test_db_side_pagination_is_complete_and_stable(client, big_dataset):
    params = {"search": "Werkzeugbruch Drehmoment", "sort": "severity", "order": "desc", "page_size": 200}
    first, _ = _timed(client, {**params, "page": 1})
    total = first["total"]
    seen, page = [], 1
    while True:
        body, _ = _timed(client, {**params, "page": page})
        if not body["rows"]:
            break
        seen += [r["id"] for r in body["rows"]]
        page += 1
    assert len(seen) == len(set(seen)) == total               # no duplicates / gaps across pages
    with SessionLocal() as db:
        expected = db.scalar(select(func.count(Investigation.id)).where(
            Investigation.search_text.like(like_pattern("Werkzeugbruch Drehmoment"), escape="\\")))
    assert total == expected
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    sev = [rank[r["severity"]] for r in first["rows"]]
    assert sev == sorted(sev, reverse=True)


def test_summary_at_50k(client, big_dataset):
    t = time.perf_counter()
    s = client.get("/api/action-center/summary").json()
    ms = (time.perf_counter() - t) * 1000
    print(f"\n  summary: {ms:.0f} ms, open_total={s['kpis']['open_total']}")
    assert ms < 5000
    with SessionLocal() as db:
        open_n = db.scalar(select(func.count(Investigation.id)).where(
            Investigation.status.in_(("open", "in_progress", "pending_verification", "reopened"))))
    assert s["kpis"]["open_total"] == open_n
    assert len(s["priority_issues"]) == 8 and len(s["trend"]) == 12


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="query plans are PostgreSQL-specific")
def test_postgres_uses_indexes(big_dataset):
    with SessionLocal() as db:
        # a selective search (an ID or a rare word) must be answered from the trigram index
        plan = "\n".join(db.execute(text(
            "EXPLAIN SELECT id FROM investigations WHERE company_id = :c AND search_text LIKE '%inv-2099-04711%'"),
            {"c": big_dataset}).scalars())
        assert "ix_inv_search_trgm" in plan, plan
        plan2 = "\n".join(db.execute(text(
            "EXPLAIN SELECT id FROM investigations WHERE company_id = :c ORDER BY created_at DESC LIMIT 25"),
            {"c": big_dataset}).scalars())
        assert "ix_inv_company_created" in plan2, plan2
        db.rollback()
