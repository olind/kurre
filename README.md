# kurre

Simple static HTML user interface to display recordings from BirdNET-Pi. Not a
database — just regenerated HTML files when rsyncing MP3s and detections.

The idea is to use an old Raspberry Pi with just a mic to record and classify
birds, then schedule an rsync job to another computer that builds the static HTML
UI. It is not aiming to replace the beautiful BirdNET-Pi UI.

It exists because a Pi 3B+ behind a firewall is too slow to serve a usable web
frontend. Disabling the frontend entirely turns the Pi into a headless
capture-and-detect box, and presentation moves to a machine that can afford it.

## What you get

```
index.html      species heard in the last 10 days, by-hour chart, every species
latest.html     every detection from the last 14 days, newest first
species/*.html  one page per species, every recording, shared audio player
assets/         style.css + app.js
By_Date/        the mp3s, foldered by date then species
birds.db        one cumulative SQLite database
```

**Fully relative and dependency-free.** No CDN, no server, no network, no build
step. It works from `file://`, a USB drive or a zip, so the whole folder can just
be handed to someone.

Audio is *referenced*, not embedded, so the HTML stays small (a 12,000-detection
index is ~50 KB) and the mp3s are shared as the folder they already are.

## Requirements

- **On the Pi:** BirdNET-Pi, `sqlite3`, and ssh access by key.
- **On the receiving machine:** `bash`, `rsync`, `sqlite3`, `python3`. No pip
  packages — the report generator uses only the standard library.

## Usage

```sh
./kurre-sync.sh              # rsync audio, merge the database, rebuild the HTML
./kurre-sync.sh --dry-run    # show what would happen, write nothing
./kurre-report.py [dir]      # rebuild the HTML only
```

`kurre-report.py` is run automatically at the end of every `kurre-sync.sh`, so
normally you only ever run the first command.

## Configuration

Copy the example and edit it:

```sh
cp kurre.conf.example kurre.conf
```

`kurre.conf` is gitignored, so your host name, user name and archive path stay out
of version control. It is looked for as `$KURRE_CONF`, then beside the scripts,
then at `~/.config/kurre/kurre.conf`.

Precedence is **environment > `kurre.conf` > built-in defaults**, so a one-off
override needs no edit:

```sh
KURRE_HOST=otherpi ./kurre-sync.sh
```

| Variable | Default | What it is |
|---|---|---|
| `KURRE_HOST` | `birdnet-pi` | ssh host of the Pi (a `~/.ssh/config` alias is fine) |
| `KURRE_REMOTE_HOME` | `/home/pi` | home directory of the BirdNET-Pi user |
| `KURRE_REMOTE_DB` | `$KURRE_REMOTE_HOME/BirdNET-Pi/scripts/birds.db` | the live database |
| `KURRE_REMOTE_AUDIO` | `$KURRE_REMOTE_HOME/BirdSongs/Extracted/By_Date/` | extraction folder |
| `KURRE_ARCHIVE` | `~/kurre-archive` | where the archive is built |
| `KURRE_RECENT_DAYS` | `10` | "Recently heard" window on `index.html` |
| `KURRE_LATEST_DAYS` | `14` | rolling window on `latest.html` |

Normally only `KURRE_HOST`, `KURRE_REMOTE_HOME` and `KURRE_ARCHIVE` need setting —
the two remote paths are derived from `KURRE_REMOTE_HOME`.

The config file is shell syntax using `:=` assignments, so `kurre-sync.sh` sources
it directly and `kurre-report.py` parses the same two forms. That is why an
environment variable always wins: `:=` only assigns when unset.

## Design decisions, and why

- **Copy, never move. No `--delete` anywhere.** The archive is a *superset* of the
  Pi: when the Pi purges old recordings to free space, the archive keeps them and
  their database rows.
- **`By_Date/` only, never `Extracted/`.** `Extracted/` is full of symlinks into
  the BirdNET-Pi install, so following them would drag the whole install —
  including the live database — into your archive.
- **`sqlite3 .backup` for the snapshot, not `cp`.** BirdNET-Pi writes to the
  database continuously and a plain copy can capture a torn page. `.backup` uses
  SQLite's online backup API and is consistent.
- **Merge in a staging dir, then atomic `mv`.** SQLite journal files inside a
  syncing cloud folder are a corruption risk, so the merge happens in `mktemp -d`
  and only the finished file is moved into place. `journal_mode=DELETE` keeps
  stray `-wal`/`-shm` files out of the cloud folder.
- **Dedup key `UNIQUE (Date, Time, Sci_Name, File_Name)` + `INSERT OR IGNORE`.**
  A re-sync is a no-op, while two genuinely different detections are never
  collapsed. This is what makes it safe to run on a schedule.
- **One shared `<audio>` per page**, not one per row. A single common species can
  have thousands of detections and a player per row would crawl.
- **Species folder names are sanitised the same way BirdNET-Pi sanitises them.**
  `'` removed, space to `_`, **`/` to `-`**. That last one matters: 109 labels in
  the Swedish label set contain a slash (e.g. `Kricka/Amerikansk kricka`), and
  treating it as a path separator silently breaks every audio link for those
  species. If you change `com_safe()`, change it in step with BirdNET-Pi's
  `Detection.common_name_safe`.
- **Charts are inline SVG**, single-series, sequential one-hue, light and dark
  both defined explicitly. Ninety-odd species is a *table*, not ninety colours.
- **No detections-per-day counts.** One bird sitting by the mic can log 50
  detections in a day, so daily counts measure loitering, not birds. Species
  pages show *days heard* instead.
- **Recording lengths come from the file size.** BirdNET-Pi's extractions are
  constant-bitrate MP3 with no Xing header, so `size × 8 / bitrate` is exact and
  needs one 4-byte read per file rather than a decode.

## Note on BirdNET-Pi config

This repo deliberately contains no BirdNET-Pi configuration. `birdnet.conf` holds
a plaintext web-UI password, an icecast password and your home coordinates — keep
it out of version control. It is in `.gitignore` here as a guard.

## Licence

GPL-3.0. See [LICENSE](LICENSE).
