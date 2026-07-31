#!/usr/bin/env bash
# Nightly backup for the Foxy Audit prod stack (Phase 5 · 5E).
#
# Dumps the `foxy` database from the compose `db` service, gzips it into
# deploy/backups/, and prunes dumps older than RETENTION_DAYS. Run from the repo
# root on the VM.
#
# P6c · IT ALSO TARS THE AVATAR VOLUME. Until that landed, every byte of prod
# state was in Postgres and a pg_dump was a complete backup. User avatars are the
# first thing that is not, so a database-only backup would now restore a
# workspace whose people have all lost their photos — quietly, and only
# noticeably long after the restore. The two artefacts are written with the SAME
# stamp so a restore can pair them without guessing.
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

# Avatars (P6c) — the only prod state outside Postgres. Tarred from inside the
# api container because the files live on a named volume, not on the host
# filesystem, so there is no host path to tar directly.
#   -C /data/avatars .  keeps the archive rooted at the directory contents, so an
#   extract restores files rather than a nested /data/avatars/data/avatars.
# `|| true` on an empty directory: no avatars yet is a normal state on a fresh
# deploy and must not fail the nightly job that also backs up the database.
AVATARS_OUT="$BACKUP_DIR/avatars_${STAMP}.tar.gz"
if docker compose -f deploy/docker-compose.prod.yml exec -T foxy-backend      tar czf - -C /data/avatars . > "$AVATARS_OUT" 2>/dev/null; then
  echo "avatars written: $AVATARS_OUT ($(du -h "$AVATARS_OUT" | cut -f1))"
else
  echo "avatars: nothing to archive (no /data/avatars yet)"
  rm -f "$AVATARS_OUT"
fi

# Retention: same window for both artefacts, so a stamp never survives with only
# half of itself left on disk.
find "$BACKUP_DIR" -name 'foxy_*.sql.gz' -type f -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name 'avatars_*.tar.gz' -type f -mtime +"$RETENTION_DAYS" -delete
echo "pruned dumps and avatar archives older than ${RETENTION_DAYS}d"
