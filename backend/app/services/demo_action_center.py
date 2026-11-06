"""SYNTHETIC Action Center demo content (fictional investigations for the fictional demo company).

Investigations are linked to the anomalies that were deliberately injected into the synthetic production
data, so the anomaly → investigation → corrective action → before/after workflow can be demonstrated.
All records carry ``is_synthetic=True`` and the UI labels them. Narratives are illustrative only.

Owners: up to three synthetic placeholder people are created (e-mail domain ``demo.factorypulse.invalid``,
random unusable password – they cannot sign in). If real analyst/admin accounts exist, some actions are
assigned to them so "My Actions" shows content for the demo presenter.
"""
from __future__ import annotations

import datetime as dt
import secrets
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Company,
    CorrectiveAction,
    Investigation,
    InvestigationAnomalyLink,
    InvestigationComment,
    InvestigationEvent,
    Machine,
    ProductionLine,
    User,
)
from ..config import get_settings
from ..models import utcnow
from . import auth as A
from .anomaly import detect
from .data_access import Filters, load_defects, load_downtime, load_records
from .settings_store import DEFAULT_ANOMALY_CONFIG

DEMO_DOMAIN = "demo.factorypulse.invalid"
DEMO_PEOPLE = [
    ("lena.schmidt", "Lena Schmidt (Demo · Instandhaltung)", "analyst"),
    ("tobias.wagner", "Tobias Wagner (Demo · Qualität)", "analyst"),
    ("sabine.koch", "Sabine Koch (Demo · Produktionsleitung)", "analyst"),
]


def _people(db: Session, company: Company) -> dict[str, User]:
    out = {}
    for local, name, role in DEMO_PEOPLE:
        email = f"{local}@{DEMO_DOMAIN}"
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(company_id=company.id, email=email, display_name=name, role=role,
                     password_hash=A.hash_password(secrets.token_urlsafe(32)), must_change_password=True)
            db.add(u)
            db.flush()
        out[local] = u
    return out


def _real(db: Session, company: Company, role: str) -> User | None:
    return db.scalar(select(User).where(User.company_id == company.id, User.role == role, User.is_active.is_(True),
                                        ~User.email.like(f"%@{DEMO_DOMAIN}")).order_by(User.id).limit(1))


def seed(db: Session, company: Company, now: dt.datetime | None = None) -> dict:
    now = now or utcnow()
    today = now.astimezone(ZoneInfo(get_settings().timezone)).date()
    ppl = _people(db, company)
    lena, tobias, sabine = ppl["lena.schmidt"], ppl["tobias.wagner"], ppl["sabine.koch"]
    real_admin, real_analyst = _real(db, company, "admin"), _real(db, company, "analyst")
    presenter = real_analyst or real_admin or sabine

    lines = {l.code: l for l in db.scalars(select(ProductionLine).where(ProductionLine.company_id == company.id))}
    machines = {m.code: m for m in db.scalars(select(Machine).where(Machine.company_id == company.id))}

    cfg = dict(DEFAULT_ANOMALY_CONFIG)
    f = Filters()
    res = detect(load_records(db, company.id, f), cfg, load_downtime(db, company.id, f), load_defects(db, company.id, f))
    anomalies = {a["id"]: a for a in res.get("anomalies", [])}
    seq = [0]

    def at(days: float, hour: int = 9) -> dt.datetime:
        d = now - dt.timedelta(days=days)
        return d.replace(hour=hour, minute=15, second=0, microsecond=0)

    def event(inv, typ, user, data, when):
        db.add(InvestigationEvent(company_id=company.id, investigation_id=inv.id, event_type=typ,
                                  actor_id=user.id if user else None,
                                  actor_label=f"{user.display_name} <{user.email}>" if user else "System", data=data,
                                  created_at=when))

    def make(title, description, category, severity, line, machine, owner, creator, created, due, status,
             anomaly_key=None, resolution=None, resolved=None, closed=None, status_changed=None):
        seq[0] += 1
        inv = Investigation(company_id=company.id, number=f"INV-{created.year}-{seq[0]:04d}", title=title,
                            description=description, category=category, severity=severity, status=status,
                            source="anomaly" if anomaly_key else "manual", line_id=lines[line].id,
                            machine_id=machines[machine].id if machine else None, owner_id=owner.id if owner else None,
                            created_by_id=creator.id if creator else None, due_date=due, resolution_summary=resolution,
                            created_at=created, updated_at=status_changed or created,
                            status_changed_at=status_changed or created, resolved_at=resolved, closed_at=closed,
                            is_synthetic=True)
        db.add(inv)
        db.flush()
        event(inv, "created", creator, {"source": inv.source, "severity": severity, "category": category,
                                        "due_date": due.isoformat() if due else None, "anomaly_key": anomaly_key,
                                        "synthetic": True}, created)
        if owner:
            event(inv, "assigned", creator, {"from": None, "to": owner.display_name}, created + dt.timedelta(minutes=5))
        if anomaly_key and anomaly_key in anomalies:
            a = anomalies[anomaly_key]
            db.add(InvestigationAnomalyLink(
                company_id=company.id, investigation_id=inv.id, anomaly_key=anomaly_key,
                anomaly_date=dt.date.fromisoformat(a["date"]), level=a["level"], entity_code=a["entity_code"],
                metric=a["metric"], snapshot={**a, "detection_config": cfg, "captured_at": created.isoformat(),
                                              "note": "Synthetic demo: evidence snapshot frozen at creation."},
                created_at=created))
        return inv

    def action(inv, title, owner, due, priority, status, creator, created, completed=None, notes=None,
               verification=None, verified_by=None, verified=None):
        a = CorrectiveAction(company_id=company.id, investigation_id=inv.id, title=title, description="",
                             owner_id=owner.id if owner else None, due_date=due, priority=priority, status=status,
                             completed_at=completed, completion_notes=notes, verification_result=verification,
                             verified_by_id=verified_by.id if verified_by else None, verified_at=verified,
                             created_by_id=creator.id if creator else None, created_at=created,
                             updated_at=verified or completed or created)
        db.add(a)
        db.flush()
        event(inv, "action_created", creator, {"action_id": a.id, "title": title,
                                               "owner": owner.display_name if owner else None,
                                               "due_date": due.isoformat() if due else None, "priority": priority}, created)
        if completed:
            event(inv, "action_status_changed", owner, {"action_id": a.id, "title": title, "from": "in_progress",
                                                        "to": "done", "notes": notes}, completed)
        if verified:
            event(inv, "action_verified", verified_by, {"action_id": a.id, "title": title, "from": "done",
                                                        "to": "verified", "verification_result": verification}, verified)
        return a

    def status(inv, user, frm, to, when, note=None, resolution=None):
        event(inv, "status_changed", user, {"from": frm, "to": to, "note": note, "resolution_summary": resolution}, when)

    def comment(inv, user, body, when):
        c = InvestigationComment(company_id=company.id, investigation_id=inv.id, author_id=user.id,
                                 author_label=user.display_name, body=body, created_at=when)
        db.add(c)
        db.flush()
        event(inv, "comment", user, {"comment_id": c.id, "body": body}, when)

    # 1 ── CLOSED: defect spike M-06 (injected anomaly 2026-08-12)
    a_date = dt.date(2026, 8, 12)
    c1 = dt.datetime.combine(a_date + dt.timedelta(days=1), dt.time(8, 30), dt.timezone.utc)
    inv1 = make("Abweichung Ausschussquote – M-06 (12.08.2026)",
                "Statistisch auffällige Ausschussquote an Fräszentrum 06 (Maßabweichungen). Ursache war bei Anlage "
                "nicht bekannt und wurde untersucht. [SYNTHETISCHES DEMO-BEISPIEL]",
                "quality_issue", "critical", "L2", "M-06", tobias, sabine, c1, a_date + dt.timedelta(days=3), "closed",
                anomaly_key="machine-M-06-20260812-defect_rate",
                resolution="Synthetic demo: shop-floor review documented a worn clamping fixture as probable cause. "
                           "Fixture replaced; defect rate observed within the previous range afterwards (see KPI comparison).",
                resolved=c1 + dt.timedelta(days=9), closed=c1 + dt.timedelta(days=12), status_changed=c1 + dt.timedelta(days=12))
    status(inv1, tobias, "open", "in_progress", c1 + dt.timedelta(hours=2))
    action(inv1, "Spannvorrichtung an M-06 prüfen und Verschleiß dokumentieren", lena, a_date + dt.timedelta(days=3),
           "high", "verified", tobias, c1 + dt.timedelta(hours=3), completed=c1 + dt.timedelta(days=1, hours=4),
           notes="Verschleiß an Spannbacken festgestellt, Vorrichtung getauscht.", verification="effective",
           verified_by=tobias, verified=c1 + dt.timedelta(days=8))
    action(inv1, "Erstteilprüfung nach Werkzeugwechsel (SPC) für 2 Wochen verschärfen", tobias,
           a_date + dt.timedelta(days=16), "medium", "verified", tobias, c1 + dt.timedelta(hours=3),
           completed=c1 + dt.timedelta(days=8), notes="SPC-Auswertung ohne Auffälligkeiten.",
           verification="effective", verified_by=sabine, verified=c1 + dt.timedelta(days=9))
    comment(inv1, tobias, "Teile aus der Charge vom 12.08. wurden gesperrt und nachgemessen.", c1 + dt.timedelta(hours=1))
    status(inv1, tobias, "in_progress", "pending_verification", c1 + dt.timedelta(days=8, hours=1))
    status(inv1, tobias, "pending_verification", "resolved", c1 + dt.timedelta(days=9), resolution=inv1.resolution_summary)
    status(inv1, sabine, "resolved", "closed", c1 + dt.timedelta(days=12), resolution=inv1.resolution_summary)

    # 2 ── RESOLVED (this month): production drop line L3 (injected 2026-08-26)
    c2 = dt.datetime.combine(dt.date(2026, 8, 27), dt.time(7, 45), dt.timezone.utc)
    r2 = max(now - dt.timedelta(days=min(5, max(today.day - 1, 0))), c2 + dt.timedelta(days=3))
    inv2 = make("Produktionseinbruch – Linie 3 (26.08.2026)",
                "Zielerreichung der gesamten Linie 3 deutlich unter Basislinie; erfasste Stillstandsgründe: Materialmangel. "
                "[SYNTHETISCHES DEMO-BEISPIEL]", "production_loss", "critical", "L3", None, sabine, sabine, c2,
                dt.date(2026, 9, 5), "resolved", anomaly_key="line-L3-20260826-target_achievement",
                resolution="Synthetic demo: late component delivery documented by logistics; safety stock introduced. "
                           "Effect on output to be confirmed over a longer period.",
                resolved=r2, status_changed=r2)
    status(inv2, sabine, "open", "in_progress", c2 + dt.timedelta(hours=1))
    action(inv2, "Lieferterminabweichung mit Lieferant klären", sabine, dt.date(2026, 9, 1), "high", "verified", sabine,
           c2 + dt.timedelta(hours=2), completed=c2 + dt.timedelta(days=2), notes="Lieferant bestätigt Engpass; neue Liefertermine.",
           verification="partially_effective", verified_by=sabine, verified=r2 - dt.timedelta(days=1))
    action(inv2, "Sicherheitsbestand für Antriebskomponente festlegen", presenter, dt.date(2026, 9, 10), "medium", "done",
           sabine, c2 + dt.timedelta(hours=2), completed=c2 + dt.timedelta(days=9), notes="Sicherheitsbestand = 2 Tagesbedarfe.")
    status(inv2, sabine, "in_progress", "pending_verification", c2 + dt.timedelta(days=10))
    status(inv2, sabine, "pending_verification", "resolved", r2, resolution=inv2.resolution_summary)

    # 3 ── PENDING VERIFICATION too long: breakdown M-02 (injected 2026-09-02)
    c3 = dt.datetime.combine(dt.date(2026, 9, 3), dt.time(6, 40), dt.timezone.utc)
    pv3 = now - dt.timedelta(days=9)
    inv3 = make("Auffälliger ungeplanter Stillstand – M-02 (02.09.2026)",
                "Mechanische Störung in der Nachtschicht mit ca. 300 min Stillstand erfasst. [SYNTHETISCHES DEMO-BEISPIEL]",
                "excessive_downtime", "medium", "L1", "M-02", lena, sabine, c3, today - dt.timedelta(days=2),
                "pending_verification", anomaly_key="machine-M-02-20260902-downtime_ratio", status_changed=pv3)
    status(inv3, lena, "open", "in_progress", c3 + dt.timedelta(hours=1))
    action(inv3, "Instandhaltungsauftrag für Spindellager auswerten", lena, dt.date(2026, 9, 8), "medium", "done", lena,
           c3 + dt.timedelta(hours=2), completed=c3 + dt.timedelta(days=4), notes="Lager getauscht, Auftrag IH-2026-0815 (fiktiv).")
    status(inv3, lena, "in_progress", "pending_verification", pv3)

    # 4 ── IN PROGRESS critical, overdue action: performance loss M-07 (injected 2026-09-16)
    c4 = now - dt.timedelta(days=12)
    inv4 = make("Leistungsverlust – M-07 (16.09.2026)",
                "Leistungsgrad ca. 55 % ohne erfassten Stillstand (Verlangsamung). Ursache nicht festgestellt. "
                "[SYNTHETISCHES DEMO-BEISPIEL]", "production_loss", "critical", "L2", "M-07", presenter, sabine, c4,
                today + dt.timedelta(days=3), "in_progress", anomaly_key="machine-M-07-20260916-performance",
                status_changed=c4 + dt.timedelta(hours=2))
    status(inv4, presenter, "open", "in_progress", c4 + dt.timedelta(hours=2))
    action(inv4, "Vorschubparameter gegen Programmstandard prüfen", presenter, today - dt.timedelta(days=3), "high",
           "in_progress", sabine, c4 + dt.timedelta(hours=3))
    action(inv4, "Spindellast-Protokolle der Vorwoche auswerten", lena, today + dt.timedelta(days=4), "medium", "open",
           sabine, c4 + dt.timedelta(hours=3))
    comment(inv4, presenter, "Bediener berichtet von manuell reduziertem Vorschub nach Programmwechsel – wird geprüft.",
            c4 + dt.timedelta(days=1))

    # 5 ── OPEN high, chronic downtime M-10 (manual)
    c5 = now - dt.timedelta(days=5)
    inv5 = make("Dauerhaft erhöhte Stillstandszeiten an M-10",
                "M-10 (Altanlage) liegt seit Wochen deutlich über dem Stillstandsanteil der übrigen Maschinen. "
                "Die Ursache ist nicht festgestellt. [SYNTHETISCHES DEMO-BEISPIEL]", "excessive_downtime", "high", "L3",
                "M-10", lena, real_admin or sabine, c5, today + dt.timedelta(days=6), "open")
    action(inv5, "Mechanische Komponenten inspizieren", lena, today - dt.timedelta(days=2), "high", "open",
           real_admin or sabine, c5 + dt.timedelta(hours=1))
    action(inv5, "Wartungshistorie der letzten 12 Monate prüfen", presenter, today + dt.timedelta(days=3), "medium",
           "in_progress", real_admin or sabine, c5 + dt.timedelta(hours=1))
    action(inv5, "Wiederkehrende Stillstandsgründe auswerten", tobias, today - dt.timedelta(days=1), "medium", "done",
           real_admin or sabine, c5 + dt.timedelta(hours=1), completed=now - dt.timedelta(days=1),
           notes="Häufigster Grund: Mechanische Störung (siehe Pareto).")
    action(inv5, "Befunde dokumentieren", lena, today + dt.timedelta(days=5), "low", "open", real_admin or sabine,
           c5 + dt.timedelta(hours=1))
    action(inv5, "Maschinenleistung nach Maßnahme verifizieren", sabine, today + dt.timedelta(days=14), "medium", "open",
           real_admin or sabine, c5 + dt.timedelta(hours=1))

    # 6 ── OPEN critical WITHOUT owner (triggers "critical unassigned")
    c6 = now - dt.timedelta(hours=6)
    make("Wiederholte Qualitätsabweichungen an Linie 2",
         "Mehrere Rückmeldungen aus der Endprüfung zu Linie 2. Zuständigkeit noch offen. [SYNTHETISCHES DEMO-BEISPIEL]",
         "quality_issue", "critical", "L2", None, None, sabine, c6, today + dt.timedelta(days=2), "open")

    # 7 ── OPEN safety observation (administrative record only)
    c7 = now - dt.timedelta(days=2)
    inv7 = make("Ölfilm am Boden neben Prüfstand 12",
                "Administrative Erfassung einer Sicherheitsbeobachtung; ersetzt kein Arbeitsschutz-Managementsystem. "
                "[SYNTHETISCHES DEMO-BEISPIEL]", "safety_observation", "medium", "L3", "M-12", sabine, tobias, c7,
                today + dt.timedelta(days=1), "open")
    action(inv7, "Leckage lokalisieren und Bereich kennzeichnen", lena, today + dt.timedelta(days=1), "high", "open",
           sabine, c7 + dt.timedelta(hours=1))

    # 8 ── REOPENED process deviation M-03
    c8 = now - dt.timedelta(days=20)
    inv8 = make("Rüstzeit an Drehzentrum 03 über Standard", "Rüstvorgänge dauern länger als der Standard. "
                "[SYNTHETISCHES DEMO-BEISPIEL]", "process_deviation", "medium", "L1", "M-03", tobias, tobias, c8,
                today + dt.timedelta(days=7), "reopened", status_changed=now - dt.timedelta(days=2))
    inv8.reopen_count = 1
    action(inv8, "Rüstablauf mit Standardarbeitsblatt abgleichen", tobias, (c8 + dt.timedelta(days=5)).date(), "medium",
           "verified", tobias, c8 + dt.timedelta(hours=2), completed=c8 + dt.timedelta(days=4),
           notes="Standardarbeitsblatt aktualisiert.", verification="not_effective", verified_by=sabine,
           verified=now - dt.timedelta(days=2, hours=1))
    status(inv8, tobias, "open", "in_progress", c8 + dt.timedelta(hours=1))
    status(inv8, tobias, "in_progress", "pending_verification", c8 + dt.timedelta(days=5))
    status(inv8, tobias, "pending_verification", "resolved", c8 + dt.timedelta(days=6), resolution="Standardarbeitsblatt angepasst.")
    status(inv8, sabine, "resolved", "reopened", now - dt.timedelta(days=2),
           note="Verifikation: Rüstzeiten weiterhin über Standard (Maßnahme nicht wirksam).")
    db.flush()
    return {"investigations": seq[0], "anomalies_linked": sum(1 for k in (
        "machine-M-06-20260812-defect_rate", "line-L3-20260826-target_achievement",
        "machine-M-02-20260902-downtime_ratio", "machine-M-07-20260916-performance") if k in anomalies)}
