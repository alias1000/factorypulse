"""v0.3.1 regression: business dates come from the injectable clock in the company timezone
(Europe/Berlin) — never from the host's local date. Covers UTC midnight, the year boundary and
both daylight-saving transitions."""
import datetime as dt
import os
import time

import pytest

from app import clock
from app.clock import local_day_bounds, local_today

UTC = dt.timezone.utc


def at(*a) -> dt.datetime:
    return dt.datetime(*a, tzinfo=UTC)


@pytest.mark.parametrize("instant,expected", [
    (at(2026, 10, 17, 21, 59, 59), dt.date(2026, 10, 17)),   # 23:59:59 CEST
    (at(2026, 10, 17, 22, 0, 0), dt.date(2026, 10, 18)),     # 00:00 CEST – UTC still says the 17th
    (at(2026, 12, 31, 22, 59, 59), dt.date(2026, 12, 31)),   # 23:59:59 CET
    (at(2026, 12, 31, 23, 0, 0), dt.date(2027, 1, 1)),       # new year in Berlin, not yet in UTC
    (at(2026, 3, 29, 0, 59, 59), dt.date(2026, 3, 29)),      # 01:59:59 CET, just before spring-forward
    (at(2026, 3, 29, 1, 0, 0), dt.date(2026, 3, 29)),        # 03:00 CEST
    (at(2026, 10, 24, 22, 0, 0), dt.date(2026, 10, 25)),     # 00:00 CEST on the fall-back day
])
def test_local_today_uses_company_timezone(instant, expected):
    with clock.freeze(instant):
        assert local_today() == expected


@pytest.mark.parametrize("host_tz", ["Pacific/Kiritimati", "America/Los_Angeles", "UTC"])
def test_host_timezone_is_irrelevant(host_tz):
    old = os.environ.get("TZ")
    os.environ["TZ"] = host_tz
    time.tzset()
    try:
        with clock.freeze(at(2026, 10, 17, 22, 30)):
            assert local_today() == dt.date(2026, 10, 18)
            assert clock.local_now().utcoffset() == dt.timedelta(hours=2)
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_day_lengths_on_dst_transitions():
    def hours(d):
        s, e = local_day_bounds(d)
        return (e - s).total_seconds() / 3600
    assert hours(dt.date(2026, 3, 29)) == 23
    assert hours(dt.date(2026, 10, 25)) == 25
    assert hours(dt.date(2026, 10, 17)) == 24


def test_frozen_clock_drives_model_defaults():
    from app.models import utcnow
    with clock.freeze(at(2026, 3, 29, 1, 30)) as fc:
        assert utcnow() == at(2026, 3, 29, 1, 30)
        fc.advance(hours=1)
        assert utcnow() == at(2026, 3, 29, 2, 30)
    assert abs((utcnow() - dt.datetime.now(UTC)).total_seconds()) < 5  # restored


def test_naive_provider_is_rejected():
    clock.set_clock(lambda: dt.datetime(2026, 1, 1))
    try:
        with pytest.raises(ValueError):
            clock.now_utc()
    finally:
        clock.set_clock(None)


# ----------------------------------------------------------------------------- API behaviour

@pytest.fixture(autouse=True)
def _needs_master_data(request):
    """API tests in this module create investigations and therefore need production lines."""
    if any(n in request.fixturenames for n in ("as_role", "frozen_clock")):
        request.getfixturevalue("master_data")


def _ids(c):
    meta = c.get("/api/meta").json()
    return meta["lines"][0]["id"]


def _create(c, title, **kw):
    r = c.post("/api/investigations", json={"title": title, "category": "other", "severity": kw.pop("severity", "medium"),
                                            "line_id": _ids(c), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_overdue_flips_at_berlin_midnight_not_utc_midnight(as_role, frozen_clock):
    frozen_clock.set(at(2026, 10, 17, 21, 30))          # 23:30 Berlin, 17 Oct
    c = as_role("admin")
    inv = _create(c, "TZ-overdue-midnight", due_date="2026-10-17")
    assert inv["overdue"] is False
    assert all(r["id"] != inv["id"] for r in c.get("/api/investigations", params={"overdue": True}).json()["rows"])
    frozen_clock.set(at(2026, 10, 17, 22, 0, 1))        # 00:00:01 Berlin, 18 Oct (UTC: still 17 Oct)
    got = c.get(f"/api/investigations/{inv['id']}").json()
    assert got["overdue"] is True and got["days_overdue"] == 1
    assert any(r["id"] == inv["id"] for r in c.get("/api/investigations", params={"overdue": True}).json()["rows"])
    # default due date counts from the Berlin date (18 Oct + 20 days), not the UTC date
    low = _create(c, "TZ-default-due", severity="low")
    assert low["due_date"] == "2026-11-07"


def test_investigation_number_uses_berlin_year(as_role, frozen_clock):
    frozen_clock.set(at(2026, 12, 31, 23, 30))          # 00:30 on 1 Jan 2027 in Berlin
    c = as_role("admin")
    assert _create(c, "TZ-new-year")["number"].startswith("INV-2027-")


def test_created_date_filter_respects_dst_day_boundaries(as_role, frozen_clock):
    frozen_clock.set(at(2026, 3, 28, 22, 0))
    c = as_role("admin")
    made = {}
    for name, instant in {"A": at(2026, 3, 28, 23, 30),     # 00:30 CET 29 Mar   -> 29 Mar
                          "C": at(2026, 3, 29, 21, 30),     # 23:30 CEST 29 Mar  -> 29 Mar (23 h day)
                          "B": at(2026, 3, 29, 22, 30)}.items():  # 00:30 CEST 30 Mar -> 30 Mar
        frozen_clock.set(instant)
        c = as_role("admin")                            # sessions expire after 12 h of frozen time
        made[name] = _create(c, f"TZDST-{name}")["id"]
    rows = c.get("/api/investigations", params={"search": "TZDST-", "start_date": "2026-03-29", "end_date": "2026-03-29",
                                                "page_size": 50}).json()["rows"]
    assert {r["id"] for r in rows} == {made["A"], made["C"]}
    rows = c.get("/api/investigations", params={"search": "TZDST-", "start_date": "2026-03-30", "page_size": 50}).json()["rows"]
    assert {r["id"] for r in rows} == {made["B"]}


def test_hour_based_rules_use_elapsed_time_across_fall_back(as_role, frozen_clock):
    """Critical > 48 h: the Berlin wall clock gains an hour on 25 Oct; elapsed time must decide."""
    from app.services import alerts as AL

    frozen_clock.set(at(2026, 10, 24, 12, 0))           # 14:00 CEST
    c = as_role("admin")
    me = c.get("/api/auth/me").json()["user"]["id"]
    inv = _create(c, "TZ-critical-48h", severity="critical", owner_id=me)
    key = f"inv:{inv['id']}"

    def open_alert():
        AL.run_alert_evaluation(include_repeated=False)
        return [a for a in AL.open_alerts_for_tests() if a.rule == "critical_open_too_long" and a.subject_key == key]

    frozen_clock.set(at(2026, 10, 26, 11, 59))          # 47 h 59 min elapsed (wall clock shows 48 h 59 min)
    assert open_alert() == []
    frozen_clock.set(at(2026, 10, 26, 12, 0))           # exactly 48 h elapsed
    assert len(open_alert()) == 1


def test_resolved_this_month_uses_berlin_month(as_role, frozen_clock):
    frozen_clock.set(at(2026, 10, 31, 23, 30))          # 00:30 CET, 1 Nov (UTC: still 31 Oct)
    c = as_role("admin")
    meta = c.get("/api/meta").json()
    machine = [m for m in meta["machines"] if m["line_id"] == meta["lines"][0]["id"]][-1]
    scope = {"machine_id": machine["id"], "category": "other", "severity": "low"}
    inv = _create(c, "TZ-month-boundary", severity="low", machine_id=machine["id"])
    r = c.post(f"/api/investigations/{inv['id']}/transition",
               json={"to_status": "closed", "resolution_summary": "Closed for month boundary test"})
    assert r.status_code == 200, r.text
    nov = c.get("/api/action-center/summary", params=scope).json()
    assert nov["today"] == "2026-11-01" and nov["kpis"]["resolved_this_month"] == 1
    frozen_clock.set(at(2026, 11, 30, 22, 59))          # 23:59 CET, 30 Nov -> still November
    c = as_role("admin")
    assert c.get("/api/action-center/summary", params=scope).json()["kpis"]["resolved_this_month"] == 1
    frozen_clock.set(at(2026, 11, 30, 23, 0))           # 00:00 CET, 1 Dec -> new month
    c = as_role("admin")
    dec = c.get("/api/action-center/summary", params=scope).json()
    assert dec["today"] == "2026-12-01" and dec["kpis"]["resolved_this_month"] == 0
    frozen_clock.set(at(2026, 10, 31, 22, 59))          # before the resolution: not counted in October
    c = as_role("admin")
    assert c.get("/api/action-center/summary", params=scope).json()["kpis"]["resolved_this_month"] == 0
