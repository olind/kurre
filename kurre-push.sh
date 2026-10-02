#!/usr/bin/env bash
#
# kurre-push.sh — run ON the Pi: send new recordings and a database snapshot to
# the server, where kurre-build.sh merges them and rebuilds the HTML.
#
# Copies, never deletes: when the Pi purges old recordings, the server keeps
# them. Audio goes first and the database last, so the server never learns of a
# detection before its mp3 has arrived.
#
# The target is meant to be an ssh key locked to one directory with rrsync (see
# README), so paths below are relative to that directory.
#
# Usage:  kurre-push.sh [--dry-run]
# Cron:   15 * * * *  $HOME/kurre/kurre-push.sh >> $HOME/kurre-push.log 2>&1
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for _c in "${KURRE_CONF:-}" "$SCRIPT_DIR/kurre.conf" "$HOME/.config/kurre/kurre.conf"; do
  if [ -n "$_c" ] && [ -f "$_c" ]; then . "$_c"; KURRE_CONF_USED="$_c"; break; fi
done

: "${KURRE_PI_DB:=$HOME/BirdNET-Pi/scripts/birds.db}"
: "${KURRE_PI_AUDIO:=$HOME/BirdSongs/Extracted/By_Date/}"
: "${KURRE_UPLOAD_TARGET:?set KURRE_UPLOAD_TARGET in kurre.conf, e.g. kurre-upload:}"

# "host:" (rrsync root) is used as is; a plain directory gets a trailing slash.
T="$KURRE_UPLOAD_TARGET"
case "$T" in *:) ;; *) T="${T%/}/";; esac

say(){ printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

RSYNC_OPTS=(-a --timeout=120)
[ "${1:-}" = "--dry-run" ] && RSYNC_OPTS+=(--dry-run)

# One run at a time: a slow upload must not overlap the next cron tick.
exec 9> "${TMPDIR:-/tmp}/kurre-push.lock"
flock -n 9 || { say "previous push still running — skipping"; exit 0; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

say "=== kurre-push -> $KURRE_UPLOAD_TARGET (config: ${KURRE_CONF_USED:-none}) ==="

# By_Date only: Extracted/ itself is full of symlinks into the BirdNET-Pi install.
rsync "${RSYNC_OPTS[@]}" --exclude='*.png' --stats "$KURRE_PI_AUDIO" "${T}By_Date/" \
  | awk '/Number of regular files transferred/{print "   mp3s sent: " $NF}'

# Online-backup snapshot: consistent even while birdnet_analysis is writing.
sqlite3 "$KURRE_PI_DB" ".backup '$STAGE/snapshot.db'"
say "snapshot: $(sqlite3 "$STAGE/snapshot.db" "SELECT COUNT(*) FROM detections;") detections"
# rsync writes to a temp name and renames, so the server never reads a half file.
rsync "${RSYNC_OPTS[@]}" "$STAGE/snapshot.db" "${T}snapshot.db"

say "=== done ==="
