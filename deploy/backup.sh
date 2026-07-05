#!/usr/bin/env bash
# Nightly Postgres backup for the Foxy Audit prod stack (Phase 5 · 5E).
#
# Dumps the `foxy` database from the compose `db` service, gzips it into
# deploy/backups/, and prunes dumps older than RETENTION_DAYS. Run from the repo
# root on the VM.
#
# Install (VM) — nightly at 03:15 UTC:
#   crontab -e   then add:
#   15 3 * * *  cd /home/<user>/foxy-audit && bash deploy/backup.sh >> deploy/backups/backup.log 2>&1
#
# Restore a dump:
#   gunzip -c deploy/backups/foxy_<stamp>.sql.gz | \
#     docker compose -f deploy/docker-compose.prod.yml exec -T db psql -U foxy -d foxy
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

BACKUP_DIR="deploy/backups"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/foxy_${STAMP}.sql.gz"

# --clean --if-exists makes the dump self-contained (safe to restore over an
# existing DB); --no-owner drops role assignments the restore target may not have.
docker compose -f deploy/docker-compose.prod.yml exec -T db \
  pg_dump -U foxy -d foxy --no-owner --clean --if-exists | gzip > "$OUT"

echo "backup written: $OUT ($(du -h "$OUT" | cut -f1))"

# Retention: delete dumps older than RETENTION_DAYS.
find "$BACKUP_DIR" -name 'foxy_*.sql.gz' -type f -mtime +"$RETENTION_DAYS" -delete
echo "pruned dumps older than ${RETENTION_DAYS}d"
