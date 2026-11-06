#!/usr/bin/env bash
# Restore a backup directory (e.g. from backups-offsite/) into the running stack.
#   ops/restore.sh backups-offsite/fp-backup-20261017-021500-ab12cd
# A safety backup of the current state is created automatically before restoring.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:?usage: ops/restore.sh <backup-directory>}"
NAME="$(basename "$SRC")"
[[ "$NAME" =~ ^fp-backup-[0-9]{8}-[0-9]{6}-[a-z0-9]{6}$ ]] || { echo "not a FactoryPulse backup directory: $SRC"; exit 1; }
read -r -p "Restore ${NAME}? ALL current data will be replaced and all users signed out. Type RESTORE: " ok
[ "$ok" = "RESTORE" ] || { echo aborted; exit 1; }
docker compose cp "$SRC" "backend:/data/backups/${NAME}"
docker compose exec -T backend python -m app.cli restore "${NAME}" --yes
