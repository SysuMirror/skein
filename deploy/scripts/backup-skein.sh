#!/usr/bin/env bash
# SQLite 在线备份：WAL 下用 sqlite backup API，保留最近 N 天。
set -euo pipefail
DB=${SKEIN_DB:-/srv/skein/skein.db}
DEST=${SKEIN_BACKUP_DIR:-/srv/skein/backups}
KEEP_DAYS=${SKEIN_BACKUP_DAYS:-7}
[ -f "$DB" ] || { echo "missing db: $DB" >&2; exit 1; }
install -d -m 700 "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DEST/skein-$STAMP.db"
python3 - "$DB" "$OUT" <<'PY'
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
source = sqlite3.connect(src)
target = sqlite3.connect(dst)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
chmod 600 "$OUT"
find "$DEST" -name 'skein-*.db' -mtime +"$KEEP_DAYS" -delete
echo "backup $OUT"
