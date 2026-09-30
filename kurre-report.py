#!/usr/bin/env python3
"""
kurre-report.py - generate a static, browsable HTML report from the kurre archive.

Reads birds.db and writes index.html + latest.html + species/*.html next to the
By_Date/ audio
folder. Everything is relative and dependency-free, so the whole archive folder
works from a USB drive, a zip, or file:// with no server and no network.

Usage:  kurre-report.py [archive_dir]

KURRE_LATEST_DAYS (default 14) sets the rolling window on latest.html.
"""
import html
import os
import sqlite3
import sys
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, timedelta

ARCHIVE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "KURRE_ARCHIVE",
    os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Arkiv/kurre"),
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
def column_chart(pairs, width=880, height=180, label_every=None, pad_left=44):
    """pairs: [(label, value, tooltip)] -> inline SVG string."""
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
    for frac in (0, 0.5, 1.0):
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

    total = len(rows)
    days = sorted(per_day)
    d0 = date.fromisoformat(days[0])
    d1 = date.fromisoformat(days[-1])

    # continuous day axis, so quiet days read as gaps rather than being dropped
    span = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    day_pairs = [(d[5:], per_day.get(d, 0), f"{d}: {per_day.get(d,0)} detections") for d in span]
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
    lat_span = [d for d in span if d >= lat_from]
    lat_pairs = [(d[5:], lat_per_day.get(d, 0), f"{d}: {lat_per_day.get(d,0)} detections")
                 for d in lat_span]
    lat_n = f"{len(lat):,}".replace(",", " ")

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
<section class="card recent"><h2>Latest</h2>
<p class="note">Every detection from the last {LATEST_DAYS} days &mdash; {lat_n} of them across
{len(lat_species)} species &mdash; in one chronological list with a player.</p>
<p><a class="cta" href="latest.html">Recent detections &rarr;</a></p></section>

<section class="card"><h2>Detections per day</h2>
{column_chart(day_pairs)}</section>

<section class="card"><h2>When the birds sing</h2>
<p class="note">All detections by hour of day, summed across {len(days)} days.</p>
{column_chart(hour_pairs, height=160, label_every=2)}</section>

<section class="card"><h2>Species</h2>
<p class="note">{len(per_species)} species. Click a name for every recording.</p>
<input id="filter" class="filter" type="search" placeholder="Filter species&hellip;" autocomplete="off">
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
        rel = U(f"By_Date/{d}/{com_safe(com)}/{fn}")
        lrows.append(
            f'<tr><td class="dt">{d}</td><td class="dt">{t}</td>'
            f'<td class="nm"><a href="species/{slugs[com]}.html">{E(com)}</a></td>'
            f'<td class="num">{conf:.2f}</td>'
            f'<td><button class="play" data-src="{rel}" '
            f'aria-label="Play {E(com)} {d} {t}">&#9654; Play</button></td></tr>'
        )

    busiest = max(lat_per_day.items(), key=lambda kv: kv[1]) if lat_per_day else ("-", 0)
    lstats = f"""<section class="kpis">
<div class="kpi"><span class="n">{len(lat):,}</span><span class="l">detections</span></div>
<div class="kpi"><span class="n">{len(lat_species)}</span><span class="l">species</span></div>
<div class="kpi"><span class="n">{len(lat_per_day)}</span><span class="l">days with activity</span></div>
<div class="kpi"><span class="n">{busiest[1]:,}</span><span class="l">busiest day ({busiest[0][5:]})</span></div>
</section>""".replace(",", " ")

    lbody = f"""{lstats}
<section class="card"><h2>Detections per day</h2>{column_chart(lat_pairs)}</section>

<section class="card"><h2>Recordings</h2>
<p class="note">Newest first, every species together. Filter by name, then press Play &mdash;
all rows share the one player above the table.</p>
<div class="player"><audio id="au" controls preload="none"></audio><span id="now" class="now"></span></div>
<input id="filter" class="filter" type="search" placeholder="Filter species&hellip;" autocomplete="off">
<div class="tablewrap"><table id="sp">
<thead><tr><th>Date</th><th>Time</th><th>Species</th>
<th class="num">Confidence</th><th></th></tr></thead>
<tbody>{''.join(lrows)}</tbody></table></div></section>"""

    with open(os.path.join(ARCHIVE, "latest.html"), "w") as f:
        f.write(page("Latest", lbody, depth=0,
                     subtitle=f"Last {LATEST_DAYS} days \u00b7 {lat_from} to {d1.isoformat()}"))

    # ---- one page per species -------------------------------------------
    for com, recs in order:
        sci = recs[0][2]
        best = max(r[3] for r in recs)
        sp_day = Counter(r[0] for r in recs)
        sp_pairs = [(d[5:], sp_day.get(d, 0), f"{d}: {sp_day.get(d,0)}") for d in span]

        lines = []
        for d, t, _sci, conf, fn in reversed(recs):
            rel = U(f"By_Date/{d}/{com_safe(com)}/{fn}")
            lines.append(
                f'<tr><td class="dt">{d}</td><td class="dt">{t}</td>'
                f'<td class="num">{conf:.2f}</td>'
                f'<td><button class="play" data-src="../{rel}" '
                f'aria-label="Play {E(com)} {d} {t}">&#9654; Play</button></td></tr>'
            )

        sbody = f"""<section class="kpis">
<div class="kpi"><span class="n">{len(recs)}</span><span class="l">detections</span></div>
<div class="kpi"><span class="n">{best:.2f}</span><span class="l">best confidence</span></div>
<div class="kpi"><span class="n">{len(sp_day)}</span><span class="l">days heard</span></div>
</section>
<section class="card"><h2>Detections per day</h2>{column_chart(sp_pairs)}</section>
<section class="card"><h2>Recordings</h2>
<p class="note">Newest first. Audio plays from <code>By_Date/</code> in this folder.</p>
<div class="player"><audio id="au" controls preload="none"></audio><span id="now" class="now"></span></div>
<div class="tablewrap"><table><thead><tr><th>Date</th><th>Time</th>
<th class="num">Confidence</th><th></th></tr></thead>
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
.play{border:1px solid var(--border);background:var(--surface-0);color:var(--text-primary);
  border-radius:7px;padding:4px 11px;font-size:12px;cursor:pointer;white-space:nowrap}
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

  var au = document.getElementById('au'), now = document.getElementById('now'), cur = null;
  if (au) {
    document.addEventListener('click', function (e) {
      var b = e.target.closest('.play');
      if (!b) return;
      if (cur) cur.classList.remove('on');
      b.classList.add('on'); cur = b;
      au.src = b.getAttribute('data-src');
      if (now) now.textContent = decodeURIComponent(b.getAttribute('data-src').split('/').pop());
      au.play().catch(function () {});
    });
    au.addEventListener('ended', function () { if (cur) cur.classList.remove('on'); });
  }

  var f = document.getElementById('filter');
  if (f) {
    f.addEventListener('input', function () {
      var q = f.value.trim().toLowerCase();
      document.querySelectorAll('#sp tbody tr').forEach(function (tr) {
        tr.hidden = q && tr.querySelector('.nm').textContent.toLowerCase().indexOf(q) === -1;
      });
    });
  }
})();
"""

if __name__ == "__main__":
    main()
