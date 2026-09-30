#!/usr/bin/env bash
#
# kurre-sync.sh — archive BirdNET-Pi detections from the Pi into iCloud.
#
# Copies (never deletes) audio, and merges the live SQLite database into a single
# cumulative archive database. The archive is a SUPERSET of the Pi: when the Pi
# purges old recordings to free space (FULL_DISK=purge at 95%), the archive keeps
# them, and their rows stay in the archive database.
#
# Result in the archive folder:
#   birds.db      one database, all detections ever seen
#   By_Date/      all extraction mp3s, foldered by date then species
#   sync.log      append-only run log
#
# Usage:  kurre-sync.sh [--dry-run]
#
set -euo pipefail

# ------------------------------------------------------------- configuration --
# Precedence: environment > kurre.conf > the defaults below. The config file uses
# ":=" assignments, so anything already exported from the environment wins.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for _c in "${KURRE_CONF:-}" "$SCRIPT_DIR/kurre.conf" "$HOME/.config/kurre/kurre.conf"; do
  if [ -n "$_c" ] && [ -f "$_c" ]; then . "$_c"; KURRE_CONF_USED="$_c"; break; fi
done

: "${KURRE_HOST:=birdnet-pi}"
: "${KURRE_REMOTE_HOME:=/home/pi}"
: "${KURRE_REMOTE_DB:=$KURRE_REMOTE_HOME/BirdNET-Pi/scripts/birds.db}"
: "${KURRE_REMOTE_AUDIO:=$KURRE_REMOTE_HOME/BirdSongs/Extracted/By_Date/}"
: "${KURRE_ARCHIVE:=$HOME/kurre-archive}"

REMOTE="$KURRE_HOST"
REMOTE_DB="$KURRE_REMOTE_DB"
REMOTE_AUDIO="$KURRE_REMOTE_AUDIO"
ARCHIVE="$KURRE_ARCHIVE"

# kurre-report.py reads the same config, but export anyway so an explicit
# environment override here is carried through to it unchanged.
export KURRE_ARCHIVE KURRE_LATEST_DAYS
AUDIO_DEST="$ARCHIVE/By_Date"
ARCHIVE_DB="$ARCHIVE/birds.db"
LOG="$ARCHIVE/sync.log"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$ARCHIVE"
exec > >(tee -a "$LOG") 2>&1
say(){ printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

say "=== kurre-sync starting${DRY_RUN:+ }$([ $DRY_RUN = 1 ] && echo '(DRY RUN)')  ==="
say "config: ${KURRE_CONF_USED:-none found, using built-in defaults}  host=$REMOTE  archive=$ARCHIVE"

# ---------------------------------------------------------------- preflight --
ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE" true 2>/dev/null \
  || { say "ERROR: cannot reach '$REMOTE' over ssh — aborting"; exit 1; }
mkdir -p "$AUDIO_DEST"

# -------------------------------------------------------------------- audio --
# By_Date only: Extracted/ itself is full of symlinks into the BirdNET-Pi install.
# No --delete: the archive accumulates and outlives the Pi's own purging.
say "syncing audio from $REMOTE ..."
RSYNC_OPTS=(-a --exclude='*.png')
[ "$DRY_RUN" = 1 ] && RSYNC_OPTS+=(--dry-run)
rsync "${RSYNC_OPTS[@]}" "$REMOTE:$REMOTE_AUDIO" "$AUDIO_DEST/"

AUDIO_N=$(find "$AUDIO_DEST" -type f -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')
say "audio files in archive: $AUDIO_N"

# ----------------------------------------------------------------- database --
# sqlite3 .backup uses the online backup API, so this is a consistent snapshot
# even though birdnet_analysis is writing to the database at the same time.
# A plain cp/rsync of a live SQLite file can capture a torn page.
say "snapshotting live database ..."
ssh "$REMOTE" "sqlite3 '$REMOTE_DB' \".backup /tmp/kurre-sync-snapshot.db\""
rsync -a "$REMOTE:/tmp/kurre-sync-snapshot.db" "$STAGE/snapshot.db"
ssh "$REMOTE" "rm -f /tmp/kurre-sync-snapshot.db"

SNAP_N=$(sqlite3 "$STAGE/snapshot.db" "SELECT COUNT(*) FROM detections;")
say "snapshot contains $SNAP_N detections"

if [ "$DRY_RUN" = 1 ]; then
  BEFORE=$([ -f "$ARCHIVE_DB" ] && sqlite3 "$ARCHIVE_DB" "SELECT COUNT(*) FROM detections;" || echo 0)
  say "DRY RUN: archive has $BEFORE rows, would merge $SNAP_N — no changes written"
  say "=== done ==="
  exit 0
fi

# Merge in a staging copy, never in place. SQLite journal files inside iCloud
# Drive are a corruption risk if iCloud syncs mid-write; the final mv is atomic.
WORK="$STAGE/archive.db"
[ -f "$ARCHIVE_DB" ] && cp "$ARCHIVE_DB" "$WORK"
BEFORE=$([ -f "$WORK" ] && sqlite3 "$WORK" "SELECT COUNT(*) FROM detections;" || echo 0)

sqlite3 "$WORK" > /dev/null <<SQL
PRAGMA journal_mode=DELETE;
CREATE TABLE IF NOT EXISTS detections (
  Date DATE, Time TIME,
  Sci_Name VARCHAR(100) NOT NULL, Com_Name VARCHAR(100) NOT NULL,
  Confidence FLOAT, Lat FLOAT, Lon FLOAT, Cutoff FLOAT,
  Week INT, Sens FLOAT, Overlap FLOAT, File_Name VARCHAR(100) NOT NULL);
CREATE INDEX IF NOT EXISTS detections_Com_Name  ON detections (Com_Name);
CREATE INDEX IF NOT EXISTS detections_Sci_Name  ON detections (Sci_Name);
CREATE INDEX IF NOT EXISTS detections_Date_Time ON detections (Date DESC, Time DESC);
-- Identity of a detection. Makes re-syncing the same rows a no-op, while never
-- collapsing two genuinely different detections.
CREATE UNIQUE INDEX IF NOT EXISTS detections_identity
  ON detections (Date, Time, Sci_Name, File_Name);
ATTACH DATABASE '$STAGE/snapshot.db' AS snap;
INSERT OR IGNORE INTO detections
  (Date,Time,Sci_Name,Com_Name,Confidence,Lat,Lon,Cutoff,Week,Sens,Overlap,File_Name)
  SELECT Date,Time,Sci_Name,Com_Name,Confidence,Lat,Lon,Cutoff,Week,Sens,Overlap,File_Name
  FROM snap.detections;
DETACH DATABASE snap;
SQL

AFTER=$(sqlite3 "$WORK" "SELECT COUNT(*) FROM detections;")
cp "$WORK" "$ARCHIVE_DB.tmp" && mv -f "$ARCHIVE_DB.tmp" "$ARCHIVE_DB"

say "database merged: $BEFORE -> $AFTER rows (+$((AFTER-BEFORE)) new)"
say "archive spans $(sqlite3 "$ARCHIVE_DB" "SELECT MIN(Date)||' to '||MAX(Date) FROM detections;")"
say "distinct species: $(sqlite3 "$ARCHIVE_DB" "SELECT COUNT(DISTINCT Com_Name) FROM detections;")"
say "archive size: $(du -sh "$ARCHIVE" | cut -f1)"

# -------------------------------------------------------------------- report --
# Regenerate the browsable HTML from the merged archive database.
REPORT="$(cd "$(dirname "$0")" && pwd)/kurre-report.py"
if [ -f "$REPORT" ]; then
  say "generating HTML report ..."
  python3 "$REPORT" "$ARCHIVE" 2>&1 | while IFS= read -r line; do say "  $line"; done
else
  say "WARNING: kurre-report.py not found beside this script — skipping report"
fi

say "=== done ==="
