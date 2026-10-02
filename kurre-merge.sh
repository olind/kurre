#!/usr/bin/env bash
#
# kurre-merge.sh — merge a BirdNET-Pi database snapshot into the cumulative
# archive database. Shared by kurre-sync.sh (Mac pulls) and kurre-build.sh
# (server builds from what the Pi pushed).
#
# Usage:  kurre-merge.sh SNAPSHOT_DB ARCHIVE_DB
# Prints "BEFORE AFTER" row counts on stdout.
#
set -euo pipefail

SNAP="${1:?usage: kurre-merge.sh SNAPSHOT_DB ARCHIVE_DB}"
DB="${2:?usage: kurre-merge.sh SNAPSHOT_DB ARCHIVE_DB}"
case "$SNAP" in *"'"*) echo "kurre-merge: snapshot path must not contain a quote" >&2; exit 1;; esac

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Merge in a staging copy, never in place. SQLite journal files inside a syncing
# cloud folder are a corruption risk if it syncs mid-write; the final mv is atomic.
WORK="$STAGE/archive.db"
[ -f "$DB" ] && cp "$DB" "$WORK"
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
ATTACH DATABASE '$SNAP' AS snap;
INSERT OR IGNORE INTO detections
  (Date,Time,Sci_Name,Com_Name,Confidence,Lat,Lon,Cutoff,Week,Sens,Overlap,File_Name)
  SELECT Date,Time,Sci_Name,Com_Name,Confidence,Lat,Lon,Cutoff,Week,Sens,Overlap,File_Name
  FROM snap.detections;
DETACH DATABASE snap;
SQL

AFTER=$(sqlite3 "$WORK" "SELECT COUNT(*) FROM detections;")
mkdir -p "$(dirname "$DB")"
cp "$WORK" "$DB.tmp" && mv -f "$DB.tmp" "$DB"
echo "$BEFORE $AFTER"
