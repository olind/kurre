#!/usr/bin/env python3
"""
kurre-report.py - generate a static, browsable HTML report from the kurre archive.

Reads birds.db and writes index.html + latest.html + species/*.html next to the
By_Date/ audio
folder. Everything is relative and dependency-free, so the whole archive folder
works from a USB drive, a zip, or file:// with no server and no network.

Usage:  kurre-report.py [archive_dir]

KURRE_RECENT_DAYS (default 10) sets the "Recently heard" window on index.html.
KURRE_LATEST_DAYS (default 14) sets the rolling window on latest.html.
"""
import html
import io
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, timedelta

def load_conf():
    """Apply kurre.conf, without letting it override the environment.

    The file is shell syntax using ':=' assignments so that kurre-sync.sh can
    simply source it. Here we parse the same two forms rather than shelling out:
        : "${KURRE_FOO:=value}"
        KURRE_FOO=value
    Looked up as $KURRE_CONF, then beside this script, then ~/.config/kurre/.
    Returns the path used, or None.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    pats = (re.compile(r'^\s*:\s*"\$\{(KURRE_[A-Z_]+):=(.*)\}"\s*$'),
            re.compile(r'^\s*(KURRE_[A-Z_]+)=(.*)$'))
    for cand in (os.environ.get("KURRE_CONF"),
                 os.path.join(here, "kurre.conf"),
                 os.path.expanduser("~/.config/kurre/kurre.conf")):
        if not cand or not os.path.isfile(cand):
            continue
        with io.open(cand, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].rstrip()
                for pat in pats:
                    m = pat.match(line)
                    if not m:
                        continue
                    key, val = m.group(1), m.group(2).strip().strip('"\'')
                    # environment always wins
                    if key not in os.environ:
                        os.environ[key] = os.path.expanduser(
                            os.path.expandvars(val))
                    break
        return cand
    return None


CONF_USED = load_conf()

ARCHIVE = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    os.environ.get("KURRE_ARCHIVE", "~/kurre-archive")
)

E = html.escape
def U(p):  # url-encode a relative path (Swedish chars, colons in filenames)
    return urllib.parse.quote(p)


def com_safe(name):
    """Folder name BirdNET-Pi actually wrote, for a species common name.

    Must stay identical to Detection.common_name_safe in
    BirdNET-Pi/scripts/utils/classes.py -- including the local 2026-09-25 fix
    that maps "/" to "-". 109 labels in the Swedish label set contain a slash
    (e.g. "Kricka/Amerikansk kricka"); treating it as a path separator here
    builds a two-level folder that does not exist and breaks every audio link
    for those species.
    """
    return name.replace("'", "").replace(" ", "_").replace("/", "-")


# kbps by bitrate index, MPEG-1 Layer III (all BirdNET-Pi extractions are this)
_MP3_KBPS = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)


def mp3_seconds(path):
    """Length of a constant-bitrate MP3 from its size and first frame header.

    BirdNET-Pi's sox extractions are CBR with no Xing header (verified against
    ffprobe: 128 kbps stereo before CHANNELS=1, 64 kbps mono after), so
    size * 8 / bitrate is exact and costs one 4-byte read instead of a decode.
    Returns None if the file is missing or not a plain MPEG-1 Layer III stream.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(10)
            skip = 0
            if head[:3] == b"ID3":  # syncsafe tag size, then the first frame
                skip = 10 + ((head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9])
                fh.seek(skip)
                head = fh.read(4)
    except OSError:
        return None
    if len(head) < 4 or head[0] != 0xFF or (head[1] & 0xFE) != 0xFA:
        return None
    kbps = _MP3_KBPS[head[2] >> 4]
    return (size - skip) * 8 / (kbps * 1000) if kbps else None


def fmt_len(sec):
    return f"{sec:.1f} s" if sec is not None else "&ndash;"


def slugify(name, seen):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")
    s = "-".join(f for f in s.split("-") if f) or "species"
    base, n = s, 2
    while s in seen:
        s, n = f"{base}-{n}", n + 1
    seen.add(s)
    return s


# ---------------------------------------------------------------- charts --
# Single series throughout, so: sequential one-hue, no legend (the title names
# it), recessive axes, selective direct labels, 2px gap between bars, 4px
# rounded data-ends anchored to the baseline.
def column_chart(pairs, width=880, height=180, label_every=None, pad_left=44, axis=True):
    """pairs: [(label, value, tooltip)] -> inline SVG string.

    axis=False drops the value gridlines, for presence strips whose only
    values are 0 and 1."""
    if not pairs:
        return '<p class="empty">No data.</p>'
    vals = [v for _, v, _ in pairs]
    vmax = max(vals) or 1
    n = len(pairs)
    pad_b, pad_t, pad_r = 26, 10, 8
    plot_w = width - pad_left - pad_r
    plot_h = height - pad_t - pad_b
    slot = plot_w / n
    gap = 2 if slot > 5 else 1
    bw = max(1.0, slot - gap)
    if label_every is None:
        label_every = max(1, n // 12)

    # recessive gridlines + axis labels at 0, half, max
    grid = []
    for frac in ((0, 0.5, 1.0) if axis else ()):
        y = pad_t + plot_h - frac * plot_h
        v = round(vmax * frac)
        grid.append(f'<line class="grid" x1="{pad_left}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}"/>')
        grid.append(f'<text class="axis" x="{pad_left-8}" y="{y+4:.1f}" text-anchor="end">{v}</text>')

    bars, labels = [], []
    for i, (lab, val, tip) in enumerate(pairs):
        bh = (val / vmax) * plot_h
        x = pad_left + i * slot + gap / 2
        y = pad_t + plot_h - bh
        r = min(4.0, bw / 2, bh) if bh > 0 else 0
        base = pad_t + plot_h
        if bh <= 0.5:
            bars.append(f'<rect class="bar" x="{x:.2f}" y="{base-1:.1f}" width="{bw:.2f}" height="1" data-tip="{E(tip)}"/>')
        else:
            d = (f"M{x:.2f},{base:.1f} L{x:.2f},{y+r:.2f} Q{x:.2f},{y:.2f} {x+r:.2f},{y:.2f} "
                 f"L{x+bw-r:.2f},{y:.2f} Q{x+bw:.2f},{y:.2f} {x+bw:.2f},{y+r:.2f} "
                 f"L{x+bw:.2f},{base:.1f} Z")
            bars.append(f'<path class="bar" d="{d}" data-tip="{E(tip)}"/>')
        if i % label_every == 0:
            labels.append(f'<text class="axis" x="{x+bw/2:.2f}" y="{height-8}" text-anchor="middle">{E(lab)}</text>')

    return (f'<svg class="chart" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" role="img">'
            + "".join(grid) + "".join(bars) + "".join(labels)
            + f'<line class="axis-line" x1="{pad_left}" y1="{pad_t+plot_h}" x2="{width-pad_r}" y2="{pad_t+plot_h}"/></svg>')


def page(title, body, depth=0, subtitle=""):
    up = "../" * depth
    return f"""<!doctype html>
<html lang="sv"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{E(title)}</title>
<link rel="stylesheet" href="{up}assets/style.css">
</head><body>
<div class="wrap">
<header class="head"><a class="home" href="{up}index.html">&#8592; Kurre</a>
<h1>{E(title)}</h1>{f'<p class="sub">{E(subtitle)}</p>' if subtitle else ''}</header>
{body}
<footer class="foot">Generated from <code>birds.db</code> &middot; BirdNET-Pi on <code>kurre</code></footer>
</div>
<div id="tip" class="tip" hidden></div>
<script src="{up}assets/app.js"></script>
</body></html>"""


# ------------------------------------------------------------------ main --
def main():
    db_path = os.path.join(ARCHIVE, "birds.db")
    if not os.path.exists(db_path):
        sys.exit(f"ERROR: no database at {db_path}")
    db = sqlite3.connect(db_path)
    rows = db.execute(
        "SELECT Date, Time, Sci_Name, Com_Name, Confidence, File_Name "
        "FROM detections ORDER BY Date, Time"
    ).fetchall()
    if not rows:
        sys.exit("ERROR: database has no detections")

    os.makedirs(os.path.join(ARCHIVE, "species"), exist_ok=True)
    os.makedirs(os.path.join(ARCHIVE, "assets"), exist_ok=True)

    per_species = defaultdict(list)
    per_day = Counter()
    per_hour = Counter()
    for d, t, sci, com, conf, fn in rows:
        per_species[com].append((d, t, sci, conf, fn))
        per_day[d] += 1
        per_hour[int(t[:2])] += 1

    def audio(d, com, fn, prefix=""):
        """(relative url, length cell) for one detection's recording."""
        rel = f"By_Date/{d}/{com_safe(com)}/{fn}"
        return prefix + U(rel), fmt_len(mp3_seconds(os.path.join(ARCHIVE, rel)))

    def play_btn(src, com, d, t):
        # A real link, so the file is still reachable if scripted playback
        # fails; app.js intercepts the click and uses the shared player.
        return (f'<a class="play" href="{src}" data-src="{src}" '
                f'aria-label="Play {E(com)} {d} {t}">&#9654; Play</a>')

    total = len(rows)
    days = sorted(per_day)
    d0 = date.fromisoformat(days[0])
    d1 = date.fromisoformat(days[-1])

    # continuous day axis, so quiet days read as gaps rather than being dropped
    span = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    hour_pairs = [(f"{h:02d}", per_hour.get(h, 0), f"{h:02d}:00-{h:02d}:59: {per_hour.get(h,0)} detections")
                  for h in range(24)]

    order = sorted(per_species.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    seen, slugs = set(), {}
    for com, _ in order:
        slugs[com] = slugify(com, seen)

    # ---- rolling recent window, for latest.html --------------------------
    # Anchored to the newest date in the DATABASE, not today's real date, so an
    # archive opened months later still shows its own most recent fortnight
    # rather than an empty page.
    LATEST_DAYS = max(1, int(os.environ.get("KURRE_LATEST_DAYS", "14")))
    lat_from = (d1 - timedelta(days=LATEST_DAYS - 1)).isoformat()
    lat = [r for r in rows if r[0] >= lat_from]
    lat_per_day = Counter(r[0] for r in lat)
    lat_species = {r[3] for r in lat}
    lat_n = f"{len(lat):,}".replace(",", " ")

    # ---- front-page summary: one row per species per day -----------------
    # The sync may not run daily, so the index opens on what was heard
    # recently. Grouped by day and species (with that day's best recording)
    # rather than every detection, since one stationary bird can produce 50
    # rows a day; the full flat list stays on latest.html.
    RECENT_DAYS = max(1, int(os.environ.get("KURRE_RECENT_DAYS", "10")))
    rec_from = (d1 - timedelta(days=RECENT_DAYS - 1)).isoformat()
    by_day_sp = defaultdict(list)
    for r in rows:
        if r[0] >= rec_from:
            by_day_sp[(r[0], r[3])].append(r)
    rrows, prev_day = [], None
    # newest day first; within a day, most-detected species first
    groups = sorted(by_day_sp.items(), key=lambda kv: (kv[0][0], len(kv[1])), reverse=True)
    for (d, com), recs in groups:
        _d, t, _sci, _com, conf, fn = max(recs, key=lambda r: r[4])
        src, length = audio(d, com, fn)
        cls = ' class="newday"' if d != prev_day and prev_day is not None else ""
        prev_day = d
        rrows.append(
            f'<tr{cls}><td class="dt">{d}</td>'
            f'<td class="nm"><a href="species/{slugs[com]}.html">{E(com)}</a></td>'
            f'<td class="num">{len(recs)}</td><td class="num">{conf:.2f}</td>'
            f'<td class="num">{length}</td><td>{play_btn(src, com, d, t)}</td></tr>'
        )
    rec_species = {com for _d, com in by_day_sp}

    # ---- index -----------------------------------------------------------
    stats = f"""<section class="kpis">
<div class="kpi"><span class="n">{total:,}</span><span class="l">detections</span></div>
<div class="kpi"><span class="n">{len(per_species)}</span><span class="l">species</span></div>
<div class="kpi"><span class="n">{len(days)}</span><span class="l">days with activity</span></div>
<div class="kpi"><span class="n">{(d1-d0).days+1}</span><span class="l">days covered</span></div>
</section>""".replace(",", " ")

    trows = []
    smax = len(order[0][1])
    for com, recs in order:
        best = max(r[3] for r in recs)
        first, last = recs[0][0], recs[-1][0]
        sci = recs[0][2]
        pct = len(recs) / smax * 100
        trows.append(
            f'<tr><td class="nm"><a href="species/{slugs[com]}.html">{E(com)}</a>'
            f'<span class="sci">{E(sci)}</span></td>'
            f'<td class="num">{len(recs)}</td>'
            f'<td class="barcell"><span class="minibar" style="width:{pct:.1f}%"></span></td>'
            f'<td class="num">{best:.2f}</td><td class="dt">{first}</td><td class="dt">{last}</td></tr>'
        )

    body = f"""{stats}
<section class="card"><h2>Recently heard</h2>
<p class="note">{len(rec_species)} species over the last {RECENT_DAYS} days, {rec_from} to
{d1.isoformat()}. One row per species per day; Play gives that day's most confident recording.</p>
<div class="player"><audio id="au" controls preload="none"></audio><span id="now" class="now"></span></div>
<input class="filter" data-table="recent" type="search" placeholder="Filter species&hellip;" autocomplete="off">
<div class="tablewrap"><table id="recent">
<thead><tr><th>Date</th><th>Species</th><th class="num">Detections</th>
<th class="num">Best</th><th class="num">Length</th><th></th></tr></thead>
<tbody>{''.join(rrows)}</tbody></table></div>
<p class="more"><a class="cta" href="latest.html">Every detection, last {LATEST_DAYS} days ({lat_n}) &rarr;</a></p></section>

<section class="card"><h2>When the birds sing</h2>
<p class="note">All detections by hour of day, summed across {len(days)} days.</p>
{column_chart(hour_pairs, height=160, label_every=2)}</section>

<section class="card"><h2>Species</h2>
<p class="note">{len(per_species)} species. Click a name for every recording.</p>
<input class="filter" data-table="sp" type="search" placeholder="Filter species&hellip;" autocomplete="off">
<div class="tablewrap"><table id="sp">
<thead><tr><th>Species</th><th class="num">Detections</th><th></th>
<th class="num">Best</th><th>First</th><th>Last</th></tr></thead>
<tbody>{''.join(trows)}</tbody></table></div></section>"""

    with open(os.path.join(ARCHIVE, "index.html"), "w") as f:
        f.write(page("Kurre", body, depth=0,
                     subtitle=f"BirdNET-Pi detections, {d0.isoformat()} to {d1.isoformat()}"))

    # ---- latest.html: one flat chronological list of the recent window ---
    # Flat, with a Date column, rather than grouped under per-day heading rows:
    # the species filter in app.js walks "#sp tbody tr" and reads each row's
    # ".nm" cell, so any row without one would break it. Newest-first ordering
    # groups the days visually anyway.
    lrows = []
    for d, t, _sci, com, conf, fn in reversed(lat):
        src, length = audio(d, com, fn)
        lrows.append(
            f'<tr><td class="dt">{d}</td><td class="dt">{t}</td>'
            f'<td class="nm"><a href="species/{slugs[com]}.html">{E(com)}</a></td>'
            f'<td class="num">{conf:.2f}</td><td class="num">{length}</td>'
            f'<td>{play_btn(src, com, d, t)}</td></tr>'
        )

    lstats = f"""<section class="kpis">
<div class="kpi"><span class="n">{len(lat):,}</span><span class="l">detections</span></div>
<div class="kpi"><span class="n">{len(lat_species)}</span><span class="l">species</span></div>
<div class="kpi"><span class="n">{len(lat_per_day)}</span><span class="l">days with activity</span></div>
</section>""".replace(",", " ")

    lbody = f"""{lstats}
<section class="card"><h2>Recordings</h2>
<p class="note">Newest first, every species together. Filter by name, then press Play &mdash;
all rows share the one player above the table.</p>
<div class="player"><audio id="au" controls preload="none"></audio><span id="now" class="now"></span></div>
<input class="filter" data-table="sp" type="search" placeholder="Filter species&hellip;" autocomplete="off">
<div class="tablewrap"><table id="sp">
<thead><tr><th>Date</th><th>Time</th><th>Species</th>
<th class="num">Confidence</th><th class="num">Length</th><th></th></tr></thead>
<tbody>{''.join(lrows)}</tbody></table></div></section>"""

    with open(os.path.join(ARCHIVE, "latest.html"), "w") as f:
        f.write(page("Latest", lbody, depth=0,
                     subtitle=f"Last {LATEST_DAYS} days \u00b7 {lat_from} to {d1.isoformat()}"))

    # ---- one page per species -------------------------------------------
    for com, recs in order:
        sci = recs[0][2]
        best = max(r[3] for r in recs)
        sp_day = Counter(r[0] for r in recs)
        # presence, not counts: one stationary bird can log 50 detections a day,
        # so the bar height says only "heard that day"; the count is in the tooltip
        sp_pairs = [(d[5:], 1 if sp_day.get(d) else 0,
                     f"{d}: heard ({sp_day[d]} detections)" if sp_day.get(d) else f"{d}: not heard")
                    for d in span]

        lines = []
        for d, t, _sci, conf, fn in reversed(recs):
            src, length = audio(d, com, fn, prefix="../")
            lines.append(
                f'<tr><td class="dt">{d}</td><td class="dt">{t}</td>'
                f'<td class="num">{conf:.2f}</td><td class="num">{length}</td>'
                f'<td>{play_btn(src, com, d, t)}</td></tr>'
            )

        sbody = f"""<section class="kpis">
<div class="kpi"><span class="n">{len(recs)}</span><span class="l">detections</span></div>
<div class="kpi"><span class="n">{best:.2f}</span><span class="l">best confidence</span></div>
<div class="kpi"><span class="n">{len(sp_day)}</span><span class="l">days heard</span></div>
</section>
<section class="card"><h2>Days heard</h2>{column_chart(sp_pairs, height=90, axis=False)}</section>
<section class="card"><h2>Recordings</h2>
<p class="note">Newest first. Audio plays from <code>By_Date/</code> in this folder.</p>
<div class="player"><audio id="au" controls preload="none"></audio><span id="now" class="now"></span></div>
<div class="tablewrap"><table><thead><tr><th>Date</th><th>Time</th>
<th class="num">Confidence</th><th class="num">Length</th><th></th></tr></thead>
<tbody>{''.join(lines)}</tbody></table></div></section>"""

        with open(os.path.join(ARCHIVE, "species", f"{slugs[com]}.html"), "w") as f:
            f.write(page(com, sbody, depth=1, subtitle=sci))

    with open(os.path.join(ARCHIVE, "assets", "style.css"), "w") as f:
        f.write(CSS)
    with open(os.path.join(ARCHIVE, "assets", "app.js"), "w") as f:
        f.write(JS)

    print(f"report: {total} detections, {len(per_species)} species -> {ARCHIVE}/index.html")
    print(f"        latest: {len(lat)} detections over the last {LATEST_DAYS} days -> latest.html")


CSS = """/* Palette: validated reference instance. Single-hue sequential (blue),
   both light and dark selected rather than auto-flipped. */
:root{
  color-scheme: light;
  --surface-0:#f4f3f0; --surface-1:#fcfcfb; --border:#e3e2dd;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#78766f;
  --series-1:#2a78d6; --series-dim:#cde2fb; --grid:#eceae5;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  color-scheme: dark;
  --surface-0:#111110; --surface-1:#1a1a19; --border:#2e2e2b;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8d8b81;
  --series-1:#3987e5; --series-dim:#184f95; --grid:#262623;
}}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface-0:#111110; --surface-1:#1a1a19; --border:#2e2e2b;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8d8b81;
  --series-1:#3987e5; --series-dim:#184f95; --grid:#262623;
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);
  font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif;
  padding-top:env(safe-area-inset-top,0);padding-bottom:env(safe-area-inset-bottom,0)}
.wrap{max-width:960px;margin:0 auto;padding:24px 16px 48px}
.head{margin-bottom:20px}
.home{color:var(--text-muted);text-decoration:none;font-size:13px}
.home:hover{color:var(--series-1)}
h1{font-size:26px;margin:6px 0 2px;letter-spacing:-.01em}
h2{font-size:15px;margin:0 0 12px;font-weight:600}
.sub{margin:0;color:var(--text-secondary);font-style:italic}
.note{margin:-6px 0 12px;color:var(--text-muted);font-size:13px}
.cta{display:inline-block;padding:9px 16px;border-radius:8px;background:var(--series-1);
  color:#fff;text-decoration:none;font-weight:600;font-size:14px}
.cta:hover{filter:brightness(1.08)}
.cta:focus-visible{outline:2px solid var(--text-primary);outline-offset:2px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;
  padding:18px;margin-bottom:16px}
.kpis{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}
.kpi{flex:1 1 130px;background:var(--surface-1);border:1px solid var(--border);
  border-radius:10px;padding:14px 16px}
.kpi .n{display:block;font-size:26px;font-weight:600;letter-spacing:-.02em}
.kpi .l{display:block;color:var(--text-muted);font-size:12px;margin-top:2px}
.chart{width:100%;height:auto;display:block;overflow:visible}
.bar{fill:var(--series-1)}
.bar:hover{fill:var(--text-primary)}
.grid{stroke:var(--grid);stroke-width:1}
.axis-line{stroke:var(--border);stroke-width:1}
.axis{fill:var(--text-muted);font-size:10px}
.filter{width:100%;padding:9px 12px;margin-bottom:12px;border-radius:8px;
  border:1px solid var(--border);background:var(--surface-0);color:var(--text-primary);font-size:14px}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--text-muted);font-weight:500;font-size:12px;
  padding:6px 10px 6px 0;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:7px 10px 7px 0;border-bottom:1px solid var(--grid);vertical-align:middle}
tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
th.num{text-align:right}
.dt{color:var(--text-secondary);font-variant-numeric:tabular-nums;white-space:nowrap}
.nm a{color:var(--text-primary);text-decoration:none;font-weight:500}
.nm a:hover{color:var(--series-1)}
.sci{display:block;color:var(--text-muted);font-size:11px;font-style:italic}
.barcell{width:34%;min-width:80px}
.minibar{display:block;height:7px;border-radius:4px;background:var(--series-1);min-width:2px}
.play{display:inline-block;border:1px solid var(--border);background:var(--surface-0);
  color:var(--text-primary);text-decoration:none;
  border-radius:7px;padding:4px 11px;font-size:12px;cursor:pointer;white-space:nowrap}
.now a{color:var(--series-1)}
tr.newday td{border-top:2px solid var(--border)}
.more{margin:14px 0 0}
.play:hover{border-color:var(--series-1);color:var(--series-1)}
.play.on{background:var(--series-1);border-color:var(--series-1);color:#fff}
.player{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px;
  position:sticky;top:env(safe-area-inset-top,0);background:var(--surface-1);
  padding:8px 0;z-index:5}
.player audio{height:34px}
.now{color:var(--text-muted);font-size:12px}
.tip{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);
  padding:5px 9px;border-radius:6px;font-size:12px;white-space:nowrap;z-index:50}
.foot{margin-top:28px;color:var(--text-muted);font-size:12px;text-align:center}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.empty{color:var(--text-muted)}
@media(max-width:520px){h1{font-size:21px}.kpi .n{font-size:21px}.barcell{display:none}}
"""

JS = """// Hover tooltips for chart marks, and one shared audio player per page.
(function () {
  var tip = document.getElementById('tip');
  if (tip) {
    document.addEventListener('mouseover', function (e) {
      var t = e.target.closest('[data-tip]');
      if (!t) return;
      tip.textContent = t.getAttribute('data-tip');
      tip.hidden = false;
    });
    document.addEventListener('mousemove', function (e) {
      if (tip.hidden) return;
      var x = e.clientX + 12, y = e.clientY - 30;
      if (x + tip.offsetWidth > innerWidth - 8) x = e.clientX - tip.offsetWidth - 12;
      if (y < 4) y = e.clientY + 18;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });
    document.addEventListener('mouseout', function (e) {
      if (e.target.closest('[data-tip]')) tip.hidden = true;
    });
  }

  // Play buttons are plain links to the mp3, so the file stays reachable if
  // scripted playback fails; here they are routed into the shared player.
  // Failures are shown, never swallowed, with the direct link as a way out.
  var au = document.getElementById('au'), now = document.getElementById('now'), cur = null;
  function say(text, href) {
    if (!now) return;
    now.textContent = text;
    if (href) {
      var a = document.createElement('a');
      a.href = href; a.textContent = 'open the file directly';
      now.appendChild(document.createTextNode(' \\u2014 '));
      now.appendChild(a);
    }
  }
  if (au) {
    document.addEventListener('click', function (e) {
      var b = e.target.closest('.play');
      if (!b || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
      e.preventDefault();
      if (cur) cur.classList.remove('on');
      b.classList.add('on'); cur = b;
      au.src = b.getAttribute('data-src');
      au.load();
      say(decodeURIComponent(au.getAttribute('src').split('/').pop()));
      var p = au.play();
      if (p && p.catch) p.catch(function (err) {
        if (err && err.name === 'AbortError') return;  // superseded by a newer click
        say('Could not play: ' + (err && (err.message || err.name)), b.getAttribute('href'));
      });
    });
    au.addEventListener('error', function () {
      var c = au.error ? au.error.code : '?';
      say('Could not load this recording (media error ' + c + ')', au.getAttribute('src'));
      if (cur) cur.classList.remove('on');
    });
    au.addEventListener('ended', function () { if (cur) cur.classList.remove('on'); });
  }

  document.querySelectorAll('.filter[data-table]').forEach(function (f) {
    var rows = document.querySelectorAll('#' + f.getAttribute('data-table') + ' tbody tr');
    f.addEventListener('input', function () {
      var q = f.value.trim().toLowerCase();
      rows.forEach(function (tr) {
        var nm = tr.querySelector('.nm');
        tr.hidden = !!(q && nm && nm.textContent.toLowerCase().indexOf(q) === -1);
      });
    });
  });
})();
"""

if __name__ == "__main__":
    main()
