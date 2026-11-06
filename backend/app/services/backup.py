"""Backup & restore (PostgreSQL ``pg_dump``/``pg_restore`` + private files).

A backup is a directory ``<data_dir>/backups/<name>/`` containing

* ``database.dump``  – pg_dump custom format (session table data excluded)
* ``files.tar.gz``    – uploads/ and reports/
* ``manifest.json``   – app version, Alembic revision, SHA-256 checksums, creator, timestamps

Restore verifies checksums and schema compatibility, takes an automatic *pre-restore safety
backup*, restores the database in ONE transaction (``--single-transaction``), migrates it to
the current schema, replaces the files and finally revokes all sessions.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from .. import __version__
from ..config import get_settings
from ..models import utcnow

log = logging.getLogger("factorypulse.backup")
_lock = threading.Lock()
NAME_RE = re.compile(r"^fp-backup-\d{8}-\d{6}-[a-z0-9]{6}$")
FORMAT_VERSION = 1


class BackupError(RuntimeError):
    pass


@dataclass
class PgConn:
    host: str
    port: str
    user: str
    password: str
    database: str

    def env(self) -> dict[str, str]:
        e = dict(os.environ)
        e["PGPASSWORD"] = self.password
        return e

    def args(self) -> list[str]:
        return ["-h", self.host, "-p", self.port, "-U", self.user]


def _pg() -> PgConn:
    url = make_url(get_settings().database_url)
    if not url.drivername.startswith("postgresql"):
        raise BackupError("Backups are only supported for PostgreSQL databases.")
    return PgConn(url.host or "localhost", str(url.port or 5432), url.username or "", url.password or "",
                  url.database or "")


def _bin(name: str) -> str:
    d = get_settings().pg_bin_dir
    path = str(Path(d) / name) if d else shutil.which(name)
    if not path or not Path(path).exists():
        raise BackupError(f"'{name}' not found. Install the PostgreSQL client tools (same major version as the server).")
    return path


def tool_version(name: str) -> int | None:
    try:
        out = subprocess.run([_bin(name), "--version"], capture_output=True, text=True, timeout=10).stdout
    except (BackupError, OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)(?:\.\d+)?", out)
    return int(m.group(1)) if m else None


def server_major(engine) -> int | None:
    if engine.dialect.name != "postgresql":
        return None
    with engine.connect() as c:
        return int(c.execute(text("SHOW server_version_num")).scalar()) // 10000


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def current_revision(engine) -> str | None:
    with engine.connect() as c:
        try:
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            return None


def known_revisions() -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    return [r.revision for r in script.walk_revisions()]  # head first


def head_revision() -> str:
    return known_revisions()[0]


def _run(cmd: list[str], env: dict[str, str], timeout: int = 1800) -> None:
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        # Never log the environment (contains the password); stderr of pg tools does not.
        raise BackupError(f"{Path(cmd[0]).name} failed: {res.stderr.strip()[:800]}")


def list_backups() -> list[dict[str, Any]]:
    out = []
    for d in sorted(get_settings().backup_dir.glob("fp-backup-*"), reverse=True):
        m = d / "manifest.json"
        if d.is_dir() and m.exists():
            try:
                man = json.loads(m.read_text())
            except json.JSONDecodeError:
                continue
            man["name"] = d.name
            man["total_size"] = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
            out.append(man)
    return out


def get_backup_dir(name: str) -> Path:
    if not NAME_RE.match(name):
        raise BackupError("Invalid backup name")
    d = get_settings().backup_dir / name
    if not (d / "manifest.json").exists():
        raise BackupError("Backup not found")
    return d


def create_backup(engine, created_by: str | None, kind: str = "manual", note: str | None = None) -> dict[str, Any]:
    if not _lock.acquire(blocking=False):
        raise BackupError("Another backup or restore is in progress.")
    try:
        return _create(engine, created_by, kind, note)
    finally:
        _lock.release()


def _create(engine, created_by, kind, note, do_prune: bool = True) -> dict[str, Any]:
    s = get_settings()
    pg = _pg()
    srv, dump_v = server_major(engine), tool_version("pg_dump")
    if dump_v is None or (srv and dump_v < srv):
        raise BackupError(f"pg_dump version {dump_v} cannot dump PostgreSQL {srv}; install client tools ≥ {srv}.")
    now = utcnow()
    name = f"fp-backup-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    tmp = s.backup_dir / f".{name}.partial"
    tmp.mkdir(parents=True)
    try:
        dump = tmp / "database.dump"
        _run([_bin("pg_dump"), *pg.args(), "-d", pg.database, "-Fc", "--no-owner", "--no-privileges",
              "--exclude-table-data=user_sessions", "-f", str(dump)], pg.env())
        files = tmp / "files.tar.gz"
        count = 0
        with tarfile.open(files, "w:gz") as tar:
            # evidence/quarantine holds unverified evidence (v0.3.1) and must be backed up as well
            # v0.3.3: evidence/orphaned keeps unreferenced uploads found by the reconciliation job
            # Order matters: quarantine is listed BEFORE evidence, so a file that is released/scanned clean
            # (quarantine → evidence) while the archive is written is still found in evidence/.
            for sub in ("uploads", "reports", "evidence/quarantine", "evidence/orphaned", "evidence"):
                base = s.data_dir / sub
                if base.exists():
                    for f in sorted(base.iterdir()):
                        if f.is_file():
                            try:
                                tar.add(f, arcname=f"{sub}/{f.name}")
                            except FileNotFoundError:
                                if not sub.startswith("evidence/"):
                                    raise
                                continue   # moved meanwhile – archived from its new location
                            count += 1
        manifest = {
            "format_version": FORMAT_VERSION, "app_version": __version__, "build": s.build_sha,
            "alembic_revision": current_revision(engine), "created_at": now.isoformat(), "created_by": created_by,
            "kind": kind, "note": note, "pg_server_major": srv, "pg_dump_major": dump_v,
            "database": {"file": dump.name, "size": dump.stat().st_size, "sha256": _sha256(dump)},
            "files": {"file": files.name, "size": files.stat().st_size, "sha256": _sha256(files), "count": count},
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))
        tmp.rename(s.backup_dir / name)  # atomic publish: incomplete backups never appear in the list
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if do_prune:
        prune(keep=s.backup_retention)
    manifest["name"] = name
    log.info("Backup created: %s (%s)", name, kind)
    return manifest


def prune(keep: int) -> list[str]:
    """Keep the newest ``keep`` backups (pre-restore safety backups count too)."""
    removed = []
    for b in list_backups()[keep:]:
        shutil.rmtree(get_settings().backup_dir / b["name"], ignore_errors=True)
        removed.append(b["name"])
    return removed


def verify_backup(d: Path) -> dict[str, Any]:
    man = json.loads((d / "manifest.json").read_text())
    if man.get("format_version") != FORMAT_VERSION:
        raise BackupError("Unsupported backup format version")
    for key in ("database", "files"):
        f = d / man[key]["file"]
        if not f.exists() or _sha256(f) != man[key]["sha256"]:
            raise BackupError(f"Checksum mismatch for {man[key]['file']} – backup is damaged or was modified.")
    rev = man.get("alembic_revision")
    if rev not in known_revisions():
        raise BackupError(f"Backup schema revision '{rev}' is unknown to this application version (backup is newer?).")
    return man


def delete_backup(name: str) -> None:
    shutil.rmtree(get_backup_dir(name))


def restore_backup(engine, name_or_path: str | Path, restored_by: str | None, safety_backup: bool = True) -> dict[str, Any]:
    if not _lock.acquire(blocking=False):
        raise BackupError("Another backup or restore is in progress.")
    try:
        d = Path(name_or_path) if isinstance(name_or_path, Path) else get_backup_dir(name_or_path)
        man = verify_backup(d)
        pg = _pg()
        # no pruning here: it must never remove the backup that is about to be restored
        safety = _create(engine, restored_by, "pre_restore", f"automatic safety backup before restoring {d.name}",
                         do_prune=False) if safety_backup else None
        engine.dispose()  # drop pooled connections so DROP/CREATE does not wait on idle sessions
        _run([_bin("pg_restore"), *pg.args(), "-d", pg.database, "--clean", "--if-exists", "--no-owner",
              "--no-privileges", "--single-transaction", "--exit-on-error", str(d / man["database"]["file"])], pg.env())
        _migrate_to_head()
        _restore_files(d / man["files"]["file"])
        with engine.begin() as c:  # nobody keeps a session across a restore
            c.execute(text("DELETE FROM user_sessions"))
        reconciled = _reconcile_evidence()
        log.warning("Backup %s restored by %s (safety backup: %s)", d.name, restored_by, safety and safety["name"])
        return {"restored": d.name, "backup_created_at": man["created_at"], "safety_backup": safety and safety["name"],
                "schema_revision_before_migration": man["alembic_revision"], "evidence_reconciliation": reconciled}
    finally:
        _lock.release()


def _reconcile_evidence() -> dict | None:
    """v0.3.3: bring the restored evidence files in line with the restored database (pending outbox
    operations from the backup moment, misplaced files). Best effort – the worker repeats it."""
    from ..db import SessionLocal
    from . import evidence_store

    try:
        with SessionLocal() as db:
            counts = evidence_store.reconcile(db)
            db.commit()
        return counts
    except Exception:  # noqa: BLE001
        log.exception("Evidence reconciliation after restore failed; the worker will retry")
        return None


def _migrate_to_head() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


def _restore_files(archive: Path) -> None:
    s = get_settings()
    with tempfile.TemporaryDirectory(dir=s.data_dir) as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                parts = Path(m.name).parts
                nested_ok = len(parts) == 3 and parts[:2] in (("evidence", "quarantine"), ("evidence", "orphaned"))
                if not m.isfile() or m.name.startswith("/") or ".." in parts or parts[0] not in ("uploads", "reports", "evidence") \
                        or not (len(parts) == 2 or nested_ok):
                    raise BackupError(f"Unsafe entry in files archive: {m.name}")
            tar.extractall(tmp, filter="data")
        for sub in ("uploads", "reports", "evidence"):
            target = s.data_dir / sub
            old = s.data_dir / f".{sub}.old"
            shutil.rmtree(old, ignore_errors=True)
            if target.exists():
                target.rename(old)
            src = Path(tmp) / sub
            if src.exists():
                src.rename(target)
            else:
                target.mkdir()
            shutil.rmtree(old, ignore_errors=True)
        s.quarantine_dir.mkdir(parents=True, exist_ok=True)


def last_backup_age_hours() -> float | None:
    b = list_backups()
    if not b:
        return None
    created = dt.datetime.fromisoformat(b[0]["created_at"])
    return (utcnow() - created).total_seconds() / 3600
