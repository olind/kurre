#!/usr/bin/env bash
#
# kurre-build.sh — run ON the server: take what kurre-push.sh uploaded to the
# inbox, merge it into the archive database and rebuild the HTML.
#
# Three directories, deliberately separate:
#   KURRE_INBOX    where the Pi uploads; its key can write nowhere else
#   KURRE_DB       the cumulative database, OUTSIDE the web root: every row
#                  carries the station's Lat/Lon
#   KURRE_ARCHIVE  the web root: generated HTML plus By_Date/*.mp3, nothing else
#
# Only *.mp3 files are copied from the inbox to the web root, so nothing else
# the Pi uploads can ever be served. Copies are hard links where possible, so
# the audio is not stored twice.
#
# Does nothing unless a new snapshot has arrived since the last build, so it is
# cheap to run often.
#
# Usage:  kurre-build.sh [--force]
# Cron:   */10 * * * *  /opt/kurre/kurre-build.sh >> /srv/kurre/build.log 2>&1
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for _c in "${KURRE_CONF:-}" "$SCRIPT_DIR/kurre.conf" "$HOME/.config/kurre/kurre.conf"; do
  if [ -n "$_c" ] && [ -f "$_c" ]; then . "$_c"; KURRE_CONF_USED="$_c"; break; fi
done

: "${KURRE_INBOX:?set KURRE_INBOX in kurre.conf}"
: "${KURRE_ARCHIVE:?set KURRE_ARCHIVE in kurre.conf}"
: "${KURRE_DB:?set KURRE_DB in kurre.conf (outside the web root)}"
export KURRE_ARCHIVE KURRE_DB

say(){ printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

SNAP="$KURRE_INBOX/snapshot.db"
STAMP="$(dirname "$KURRE_DB")/.kurre-last-build"
[ -f "$SNAP" ] || exit 0                                   # nothing uploaded yet
if [ "${1:-}" != "--force" ] && [ -f "$STAMP" ] && ! [ "$SNAP" -nt "$STAMP" ]; then
  exit 0                                                    # nothing new
fi

exec 9> "$(dirname "$KURRE_DB")/.kurre-build.lock"
flock -n 9 || { say "previous build still running — skipping"; exit 0; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
# Work on a copy, and stamp with ITS time: a push landing mid-build is then
# simply picked up by the next run.
cp -p "$SNAP" "$STAGE/snapshot.db"

say "=== kurre-build (config: ${KURRE_CONF_USED:-none}) ==="

# Audio before database, so every merged row already has its mp3 in place.
mkdir -p "$KURRE_ARCHIVE/By_Date"
# --no-links: a symlink named *.mp3 in the inbox must never reach the web root,
# where the web server would follow it out of the archive.
rsync -a --no-links --include='*/' --include='*.mp3' --exclude='*' \
  --link-dest="$KURRE_INBOX/By_Date/" "$KURRE_INBOX/By_Date/" "$KURRE_ARCHIVE/By_Date/"

COUNTS=$("$SCRIPT_DIR/kurre-merge.sh" "$STAGE/snapshot.db" "$KURRE_DB")
read -r BEFORE AFTER <<< "$COUNTS"
say "database merged: $BEFORE -> $AFTER rows (+$((AFTER-BEFORE)) new)"

python3 "$SCRIPT_DIR/kurre-report.py" "$KURRE_ARCHIVE" | while IFS= read -r line; do say "  $line"; done

touch -r "$STAGE/snapshot.db" "$STAMP"
say "=== done ==="
