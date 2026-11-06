#!/usr/bin/env bash
# Create a FactoryPulse backup inside the running stack and copy it to a host directory.
# Schedule it with cron, e.g. daily at 02:15:
#   15 2 * * *  /opt/factorypulse/ops/backup.sh >> /var/log/factorypulse-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${FP_BACKUP_COPY_DIR:-./backups-offsite}"
mkdir -p "$DEST"
docker compose exec -T backend python -m app.cli backup --kind scheduled --note "${1:-scheduled backup}" > /dev/null
NAME=$(docker compose exec -T backend python -m app.cli list-backups | head -n1 | awk '{print $1}')
docker compose cp "backend:/data/backups/${NAME}" "${DEST}/${NAME}"
( cd "${DEST}/${NAME}" && python3 - <<'PY'
import hashlib, json, sys
m = json.load(open("manifest.json"))
for k in ("database", "files"):
    h = hashlib.sha256(open(m[k]["file"], "rb").read()).hexdigest()
    sys.exit(f"checksum mismatch: {m[k]['file']}") if h != m[k]["sha256"] else None
print("verified", m["created_at"])
PY
)
# keep the newest N copies on the host as well
ls -1dt "${DEST}"/fp-backup-* | tail -n +"$(( ${FP_BACKUP_COPY_KEEP:-30} + 1 ))" | xargs -r rm -rf
echo "$(date -Is) backup ${NAME} copied to ${DEST}"
