#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly BACKUP_DIR=/opt/projects/office_desk_gear/backups
readonly STATE_DIR=/run/office-desk-gear-backup
readonly DB_CONTAINER=office-desk-gear-db-1

[[ ! -L "$BACKUP_DIR" && ! -L "$STATE_DIR" ]]
install -d -m 0700 "$BACKUP_DIR" "$STATE_DIR"
exec 9>"$STATE_DIR/backup.lock"
flock -n 9 || { echo 'Another Office Desk & Gear backup is running'; exit 0; }

[[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$DB_CONTAINER")" == office-desk-gear ]]
[[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$DB_CONTAINER")" == db ]]

temporary=''
cleanup() {
    if [[ -n "$temporary" && "$temporary" == "$BACKUP_DIR"/.incomplete.* && ! -L "$temporary" ]]; then
        rm -rf -- "$temporary"
    fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

temporary=$(mktemp -d "$BACKUP_DIR/.incomplete.XXXXXXXX")
docker exec "$DB_CONTAINER" sh -eu -c '
    test "$POSTGRES_DB" = office_desk_gear
    exec pg_dump --no-password --format=custom --no-owner --no-acl --lock-wait-timeout=30s \
        -U "$POSTGRES_USER" -d "$POSTGRES_DB"
' >"$temporary/database.dump"
test -s "$temporary/database.dump"
docker exec -i "$DB_CONTAINER" pg_restore --list <"$temporary/database.dump" >"$temporary/database.toc"
(cd "$temporary" && sha256sum database.dump database.toc >SHA256SUMS && sha256sum --check --status SHA256SUMS)

python3 - "$temporary" <<'PY'
import datetime, hashlib, json, os
from pathlib import Path
import re, shutil, sys, uuid

temporary=Path(sys.argv[1])
root=Path('/opt/projects/office_desk_gear/backups')
assert root.resolve() == root and temporary.parent == root and temporary.name.startswith('.incomplete.')
stamp=datetime.datetime.now(datetime.UTC)
with (temporary/'database.dump').open('rb') as stream:
    digest=hashlib.file_digest(stream,'sha256').hexdigest()
manifest={
    'database':'office_desk_gear', 'project':'office-desk-gear',
    'created_at_utc':stamp.isoformat(), 'scope':'database-only', 'format':'pg_dump custom',
    'dump_bytes':(temporary/'database.dump').stat().st_size,
    'dump_sha256':digest,
}
(temporary/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
for path in temporary.iterdir():
    with path.open('rb') as stream:
        os.fsync(stream.fileno())
final=root/f'office-desk-gear-{stamp:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}'
temporary.rename(final)
directory_fd=os.open(root, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
# Only our completed snapshots are rotated, and only after a successful dump.
pattern=re.compile(r'office-desk-gear-\d{8}T\d{6}Z-[0-9a-f]{8}')
snapshots=sorted(p for p in root.iterdir() if pattern.fullmatch(p.name)
                 and p.is_dir() and not p.is_symlink()
                 and (p/'manifest.json').is_file() and (p/'SHA256SUMS').is_file())
for old in snapshots[:-14]:
    assert old.parent == root and old.resolve().parent == root
    shutil.rmtree(old)
print(f'Backup completed: {final.name}; bytes={manifest["dump_bytes"]}; keep=14')
PY
temporary=''
