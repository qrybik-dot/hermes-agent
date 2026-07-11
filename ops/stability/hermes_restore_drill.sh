#!/usr/bin/env bash
set -euo pipefail

LOCK=/var/lib/hermes-backup/hermes-restic.lock
ROOT=/srv/hermes-memory/vault
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
TARGET=/var/lib/hermes-backup/restore-drill-$RUN_ID
export HOME=/var/lib/hermes-backup
export RCLONE_CONFIG=/var/lib/hermes-backup/.config/rclone/rclone.conf
export RESTIC_PASSWORD_FILE=/var/lib/hermes-backup/.config/restic/anton-personal-restic.pass
REPOSITORY=rclone:gdrive-anton:anton-personal-restic

cleanup() {
  rm -rf "$TARGET"
}
trap cleanup EXIT
exec 9>"$LOCK"
flock -n 9
mkdir -m 700 "$TARGET"

fingerprint() {
  local root=$1 manifest=$2
  (cd "$root" && find . -type f -print0 | sort -z | xargs -0 sha256sum) >"$manifest"
  local count bytes digest
  count=$(find "$root" -type f -printf . | wc -c)
  bytes=$(find "$root" -type f -printf '%s\n' | awk '{n+=$1} END {print n+0}')
  digest=$(sha256sum "$manifest" | awk '{print $1}')
  printf '%s %s %s\n' "$count" "$bytes" "$digest"
}

mkdir -m 700 "$TARGET/source.before" "$TARGET/source.after"
rsync -a --exclude-from=/etc/hermes-backup/restic-excludes.txt "$ROOT/" "$TARGET/source.before/"
read -r source_count source_bytes source_digest < <(fingerprint "$TARGET/source.before" "$TARGET/source.before.manifest")
/usr/local/bin/restic -r "$REPOSITORY" backup "$ROOT" \
  --exclude-file /etc/hermes-backup/restic-excludes.txt \
  --tag personal-anton --tag restore-drill >/dev/null
/usr/local/bin/restic -r "$REPOSITORY" restore latest --target "$TARGET/restore" --include /srv/hermes-memory/vault --verify >/dev/null
RESTORED="$TARGET/restore/srv/hermes-memory/vault"
read -r restored_count restored_bytes restored_digest < <(fingerprint "$RESTORED" "$TARGET/restored")
rsync -a --exclude-from=/etc/hermes-backup/restic-excludes.txt "$ROOT/" "$TARGET/source.after/"
read -r final_count final_bytes final_digest < <(fingerprint "$TARGET/source.after" "$TARGET/source.after.manifest")

status=PASS
if [[ "$source_count $source_bytes $source_digest" != "$restored_count $restored_bytes $restored_digest" ]]; then
  status=FAIL
fi
if [[ "$source_count $source_bytes $source_digest" != "$final_count $final_bytes $final_digest" ]]; then
  status=SOURCE_CHANGED
fi

printf '{"status":"%s","files":%s,"bytes":%s,"source_digest":"%s","restored_digest":"%s","production_unchanged":%s}\n' \
  "$status" "$source_count" "$source_bytes" "$source_digest" "$restored_digest" \
  "$([[ "$source_digest" == "$final_digest" ]] && echo true || echo false)"
[[ "$status" == PASS ]]
