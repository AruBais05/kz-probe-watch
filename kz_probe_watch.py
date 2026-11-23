#!/usr/bin/env python3
"""
KZ Probe Watch - health of Kazakhstan's RIPE Atlas measurement network.

What it does:
  1. Downloads every RIPE Atlas probe registered in Kazakhstan (public API, no key needed).
  2. Computes statistics: status breakdown, coverage gaps by city, host networks (ASNs),
     probe lifespans, IPv6 readiness.
  3. Writes a single web page: site/index.html (interactive map + tables).

Run:
  python3 kz_probe_watch.py

If the download fails (e.g. SSL error), download the data with curl instead:
  curl -s "https://atlas.ripe.net/api/v2/probes/?country_code=KZ&page_size=500" -o probes.json
  python3 kz_probe_watch.py --from-file probes.json

Only the Python standard library is used (works with Python 3.8+).
"""

import json
import math
import os
import statistics
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone

COUNTRY = "KZ"
ATLAS_URL = f"https://atlas.ripe.net/api/v2/probes/?country_code={COUNTRY}&page_size=500"
RIPESTAT_AS = "https://stat.ripe.net/data/as-overview/data.json?resource=AS{asn}&sourceapp=kz-probe-watch"

# RIPE Atlas status ids
STATUS_NAMES = {0: "Never connected", 1: "Connected", 2: "Disconnected", 3: "Abandoned", 4: "Written off"}
STATUS_COLORS = {0: "#9e9e9e", 1: "#2e7d32", 2: "#f9a825", 3: "#c62828", 4: "#6d4c41"}

# Regional centres of Kazakhstan (approximate coordinates) - used to find coverage gaps
CITIES = {
    "Astana": (51.169, 71.449), "Almaty": (43.238, 76.946), "Shymkent": (42.315, 69.587),
    "Karaganda": (49.806, 73.085), "Aktobe": (50.283, 57.167), "Taraz": (42.900, 71.367),
    "Pavlodar": (52.287, 76.967), "Oskemen": (49.948, 82.628), "Semey": (50.411, 80.227),
    "Atyrau": (47.117, 51.883), "Kostanay": (53.214, 63.624), "Kyzylorda": (44.853, 65.509),
    "Oral": (51.233, 51.367), "Petropavl": (54.875, 69.162), "Aktau": (43.650, 51.160),
    "Turkistan": (43.297, 68.252), "Kokshetau": (53.283, 69.383), "Taldykorgan": (45.017, 78.383),
    "Zhezkazgan": (47.783, 67.767), "Konaev": (43.867, 77.067),
}
GAP_RADIUS_KM = 50  # a city counts as "covered" if a connected probe is within this distance


# ---------------------------------------------------------------- data download
def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "kz-probe-watch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_probes():
    """Follow the API's pagination and return a list of probe dicts."""
    probes, url = [], ATLAS_URL
    while url:
        print(f"  downloading {url}")
        page = fetch_json(url)
        probes.extend(page.get("results", []))
        url = page.get("next")
    return probes


def load_probes():
    if "--from-file" in sys.argv:
        path = sys.argv[sys.argv.index("--from-file") + 1]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data["results"] if isinstance(data, dict) else data
    return download_probes()


def asn_names(asns):
    """Look up network (holder) names for ASNs via RIPEstat. Failures are ignored."""
    names = {}
    for asn in sorted(asns):
        try:
            data = fetch_json(RIPESTAT_AS.format(asn=asn))
            names[asn] = data["data"].get("holder") or ""
        except Exception:
            names[asn] = ""
    return names


# ---------------------------------------------------------------- helpers
def to_dt(value):
    """Atlas gives times as unix timestamps (int) or ISO strings; return datetime or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def status_id(p):
    s = p.get("status")
    if isinstance(s, dict):
        return s.get("id")
    return s


def coords(p):
    g = p.get("geometry") or {}
    c = g.get("coordinates")
    if c and len(c) == 2 and c[0] is not None:
        return c[1], c[0]  # GeoJSON is [lon, lat]
    return None


def has_tag(p, slug):
    return any((t.get("slug") if isinstance(t, dict) else t) == slug for t in p.get("tags") or [])


def km(a, b):
    """Great-circle distance between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- analysis
def analyse(probes):
    now = datetime.now(timezone.utc)
    by_status = Counter(status_id(p) for p in probes)
    connected = [p for p in probes if status_id(p) == 1]
    abandoned = [p for p in probes if status_id(p) == 3]

    # 1. Coverage gaps: nearest connected probe to each regional centre
    connected_pts = [coords(p) for p in connected if coords(p)]
    cities = []
    for name, pt in CITIES.items():
        dists = [km(pt, c) for c in connected_pts]
        nearest = min(dists) if dists else None
        nearby = sum(1 for d in dists if d <= GAP_RADIUS_KM)
        cities.append({"city": name, "nearest_km": round(nearest) if nearest is not None else None,
                       "probes_within": nearby, "gap": nearby == 0})
    cities.sort(key=lambda c: (c["probes_within"], -(c["nearest_km"] or 0)))

    # 2. Host networks (ASNs) of connected probes
    asn_counter = Counter(p.get("asn_v4") or p.get("asn_v6") for p in connected)
    asn_counter.pop(None, None)

    # 3. Lifespan of abandoned probes (first connection -> last connection)
    lifespans, drop_years = [], Counter()
    for p in abandoned:
        first, last = to_dt(p.get("first_connected")), to_dt(p.get("last_connected"))
        if first and last and last >= first:
            lifespans.append((last - first).days)
        if last:
            drop_years[last.year] += 1

    # 3b. Probe loss by network: which ISPs lose the most probes (abandoned + written off)
    per_asn = {}
    for p in probes:
        asn = p.get("asn_v4") or p.get("asn_v6")
        if asn is None:
            continue
        d = per_asn.setdefault(asn, {"total": 0, "connected": 0, "lost": 0})
        d["total"] += 1
        if status_id(p) == 1:
            d["connected"] += 1
        if status_id(p) in (3, 4):
            d["lost"] += 1
    loss = sorted(([a, d["total"], d["connected"], d["lost"]] for a, d in per_asn.items()),
                  key=lambda r: (-r[3], -r[1]))[:15]

    # 4. IPv6 readiness among connected probes
    v6_works = sum(1 for p in connected if has_tag(p, "system-ipv6-works"))

    # Age of connected probes
    ages = [(now - to_dt(p.get("first_connected"))).days for p in connected if to_dt(p.get("first_connected"))]

    return {
        "generated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(probes),
        "status_counts": {k: v for k, v in by_status.items() if k in STATUS_NAMES},
        "connected": len(connected),
        "abandoned": len(abandoned),
        "anchors": sum(1 for p in connected if p.get("is_anchor")),
        "cities": cities,
        "gap_count": sum(1 for c in cities if c["gap"]),
        "asns": asn_counter.most_common(),
        "loss": loss,
        "lifespan_median_days": round(statistics.median(lifespans)) if lifespans else None,
        "lifespan_n": len(lifespans),
        "drop_years": sorted(drop_years.items()),
        "v6_works": v6_works,
        "connected_age_median_days": round(statistics.median(ages)) if ages else None,
    }


# ---------------------------------------------------------------- web page
# Page text in three languages. Kazakh/Russian city names are used in the coverage table.
CITY_NAMES = {
    "Astana": ("Астана", "Астана"), "Almaty": ("Алматы", "Алматы"), "Shymkent": ("Шымкент", "Шымкент"),
    "Karaganda": ("Қарағанды", "Караганда"), "Aktobe": ("Ақтөбе", "Актобе"), "Taraz": ("Тараз", "Тараз"),
    "Pavlodar": ("Павлодар", "Павлодар"), "Oskemen": ("Өскемен", "Усть-Каменогорск"), "Semey": ("Семей", "Семей"),
    "Atyrau": ("Атырау", "Атырау"), "Kostanay": ("Қостанай", "Костанай"), "Kyzylorda": ("Қызылорда", "Кызылорда"),
    "Oral": ("Орал", "Уральск"), "Petropavl": ("Петропавл", "Петропавловск"), "Aktau": ("Ақтау", "Актау"),
    "Turkistan": ("Түркістан", "Туркестан"), "Kokshetau": ("Көкшетау", "Кокшетау"),
    "Taldykorgan": ("Талдықорған", "Талдыкорган"), "Zhezkazgan": ("Жезқазған", "Жезказган"), "Konaev": ("Қонаев", "Конаев"),
}

I18N = {
  "en": {
    "h_loss": "Probe loss by network", "th_conn": "Connected", "th_lostc": "Lost", "th_rate": "Loss rate", "loss_note": "Lost = abandoned or written off. Shows where probe hosts need follow-up.",
    "sub": "How healthy is Kazakhstan's RIPE Atlas measurement network, and where are the gaps?",
    "generated": "Data generated", "c_total": "probes ever registered in Kazakhstan", "c_conn": "connected now",
    "c_aband": "abandoned", "c_gaps": "regional centres with no connected probe within {r} km",
    "c_v6": "of connected probes have working IPv6", "h_status": "Probe status", "th_status": "Status",
    "th_probes": "Probes", "th_share": "Share", "life": "Median lifespan of abandoned probes:", "days": "days",
    "years": "years", "from_n": "based on {n} probes", "h_cov": "Coverage of regional centres", "th_city": "City",
    "th_within": "Connected probes within {r} km", "th_near": "Nearest probe (km)",
    "cov_note": "Red rows = coverage gaps. City coordinates are approximate.",
    "h_asn": "Networks hosting connected probes", "th_net": "Network", "h_drop": "When abandoned probes went offline",
    "th_year": "Year", "th_lost": "Probes lost", "probe": "Probe", "anchor": "anchor", "v6": "IPv6 works",
    "yes": "yes", "no": "no",
    "foot": "Source: RIPE Atlas probe API and RIPEstat. Probe locations are approximate (RIPE Atlas publishes rounded coordinates). Built by Aruzhan Baiseit.",
    "status": ["Never connected", "Connected", "Disconnected", "Abandoned", "Written off"]},
  "kk": {
    "h_loss": "Желілер бойынша жоғалған зондтар", "th_conn": "Қосулы", "th_lostc": "Жоғалған", "th_rate": "Жоғалту үлесі", "loss_note": "Жоғалған = тасталған немесе есептен шығарылған. Зонд иелерімен қайта байланысу қажет жерлерді көрсетеді.",
    "sub": "Қазақстандағы RIPE Atlas өлшеу желісі қаншалықты сау және оның олқылықтары қайда?",
    "generated": "Деректер алынған уақыт", "c_total": "Қазақстанда тіркелген барлық зондтар", "c_conn": "қазір қосулы",
    "c_aband": "тасталған", "c_gaps": "{r} км радиуста қосулы зонд жоқ облыс орталықтары",
    "c_v6": "қосулы зондтарда IPv6 жұмыс істейді", "h_status": "Зондтардың күйі", "th_status": "Күйі",
    "th_probes": "Зондтар", "th_share": "Үлесі", "life": "Тасталған зондтардың медианалық жұмыс мерзімі:", "days": "күн",
    "years": "жыл", "from_n": "{n} зонд бойынша", "h_cov": "Облыс орталықтарының қамтылуы", "th_city": "Қала",
    "th_within": "{r} км радиустағы қосулы зондтар", "th_near": "Ең жақын зонд (км)",
    "cov_note": "Қызыл жолдар — қамту олқылықтары. Қалалардың координаттары шамамен берілген.",
    "h_asn": "Қосулы зондтар орналасқан желілер", "th_net": "Желі", "h_drop": "Тасталған зондтар қашан желіден шықты",
    "th_year": "Жыл", "th_lost": "Жоғалған зондтар", "probe": "Зонд", "anchor": "якорь", "v6": "IPv6 жұмыс істейді",
    "yes": "иә", "no": "жоқ",
    "foot": "Дереккөз: RIPE Atlas зонд API және RIPEstat. Зондтардың орны шамамен көрсетілген (RIPE Atlas дөңгелектенген координаттарды жариялайды). Авторы: Аружан Байсеит.",
    "status": ["Ешқашан қосылмаған", "Қосулы", "Ажыратылған", "Тасталған", "Есептен шығарылған"]},
  "ru": {
    "h_loss": "Потери зондов по сетям", "th_conn": "Подключены", "th_lostc": "Потеряны", "th_rate": "Доля потерь", "loss_note": "Потеряны = заброшены или списаны. Показывает, где нужно связаться с владельцами зондов.",
    "sub": "Насколько здорова сеть измерений RIPE Atlas в Казахстане и где в ней пробелы?",
    "generated": "Данные получены", "c_total": "зондов когда-либо зарегистрировано в Казахстане", "c_conn": "подключены сейчас",
    "c_aband": "заброшены", "c_gaps": "областных центров без подключённого зонда в радиусе {r} км",
    "c_v6": "подключённых зондов с работающим IPv6", "h_status": "Статус зондов", "th_status": "Статус",
    "th_probes": "Зонды", "th_share": "Доля", "life": "Медианный срок работы заброшенных зондов:", "days": "дн.",
    "years": "лет", "from_n": "по {n} зондам", "h_cov": "Покрытие областных центров", "th_city": "Город",
    "th_within": "Подключённые зонды в радиусе {r} км", "th_near": "Ближайший зонд (км)",
    "cov_note": "Красные строки — пробелы в покрытии. Координаты городов приблизительные.",
    "h_asn": "Сети, в которых работают подключённые зонды", "th_net": "Сеть", "h_drop": "Когда заброшенные зонды ушли офлайн",
    "th_year": "Год", "th_lost": "Потеряно зондов", "probe": "Зонд", "anchor": "якорь", "v6": "IPv6 работает",
    "yes": "да", "no": "нет",
    "foot": "Источник: API зондов RIPE Atlas и RIPEstat. Расположение зондов приблизительное (RIPE Atlas публикует округлённые координаты). Автор: Аружан Байсеит.",
    "status": ["Никогда не подключался", "Подключён", "Отключён", "Заброшен", "Списан"]},
}


def build_page(probes, stats, names):
    points = []
    for p in probes:
        c = coords(p)
        if not c:
            continue
        sid = status_id(p)
        asn = p.get("asn_v4") or p.get("asn_v6")
        points.append({"id": p.get("id"), "lat": c[0], "lon": c[1], "s": sid if sid in STATUS_NAMES else 0,
                       "asn": asn, "net": names.get(asn, ""), "v6": has_tag(p, "system-ipv6-works"),
                       "anchor": bool(p.get("is_anchor"))})
    data = {
        "points": points, "stats": stats, "names": {str(k): v for k, v in names.items()},
        "status_counts": [stats["status_counts"].get(k, 0) for k in range(5)],
        "colors": [STATUS_COLORS[k] for k in range(5)], "radius": GAP_RADIUS_KM,
        "cityNames": CITY_NAMES, "i18n": I18N,
    }
    return PAGE_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KZ Probe Watch - RIPE Atlas in Kazakhstan</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1d2433;background:#f6f7f9}
 header{background:#14326e;color:#fff;padding:24px} header h1{margin:0 0 6px} header p{margin:0;opacity:.85}
 .top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
 .lang button{background:transparent;color:#fff;border:1px solid rgba(255,255,255,.5);border-radius:6px;padding:5px 10px;margin-left:4px;cursor:pointer;font-weight:600}
 .lang button.on{background:#fff;color:#14326e}
 main{max-width:1100px;margin:0 auto;padding:20px}
 .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:20px}
 .card{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .card b{display:block;font-size:28px} .card span{font-size:13px;color:#5b6475}
 #map{height:520px;border-radius:10px;margin-bottom:20px}
 section{background:#fff;border-radius:10px;padding:16px 20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 h2{font-size:18px;margin-top:0}
 table{border-collapse:collapse;width:100%;font-size:14px} td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #eceef2}
 tr.gap td{background:#fdecea} .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}
 .grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px} @media(max-width:800px){.grid2{grid-template-columns:1fr}}
 small{color:#5b6475}
</style></head><body>
<header><div class="top"><div><h1>KZ Probe Watch</h1><p id="sub"></p></div>
<div class="lang"><button data-l="en">EN</button><button data-l="kk">ҚАЗ</button><button data-l="ru">РУС</button></div></div></header>
<main>
<div class="cards" id="cards"></div>
<div id="map"></div>
<div class="grid2"><section id="statusSec"></section><section id="covSec"></section></div>
<div class="grid2"><section id="asnSec"></section><section id="dropSec"></section></div>
<section id="lossSec"></section>
<p><small id="foot"></small></p>
</main>
<script>
const D = __DATA__;
const S = D.stats;
let L_ = 'en', markers = [];
const fmt = (s, o) => s.replace(/\{(\w+)\}/g, (_, k) => o[k]);
const pct = (n, d) => d ? Math.round(100 * n / d) + '%' : 'n/a';
const cityName = c => L_ === 'en' ? c : D.cityNames[c][L_ === 'kk' ? 0 : 1];

const map = L.map('map').setView([48.0, 67.0], 5);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}', {maxZoom: 16, attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors'}).addTo(map);
const pts = [...D.points].sort((a, b) => (a.s === 1) - (b.s === 1));
for (const p of pts) {
  markers.push([p, L.circleMarker([p.lat, p.lon], {radius: p.anchor ? 8 : 6, color: D.colors[p.s], fillColor: D.colors[p.s], fillOpacity: .75, weight: 1}).addTo(map)]);
}
const legend = L.control({position: 'bottomright'});
legend.onAdd = () => { const d = L.DomUtil.create('div'); d.id = 'legend'; d.style.cssText = 'background:#fff;padding:8px 10px;border-radius:6px;font-size:13px'; return d; };
legend.addTo(map);

function render(lang) {
  L_ = lang; const T = D.i18n[lang]; document.documentElement.lang = lang;
  document.querySelectorAll('.lang button').forEach(b => b.classList.toggle('on', b.dataset.l === lang));
  document.getElementById('sub').textContent = T.sub + ' ' + T.generated + ': ' + S.generated;
  document.getElementById('cards').innerHTML = [
    [S.total, T.c_total], [S.connected, T.c_conn + ' (' + pct(S.connected, S.total) + ')'],
    [S.abandoned, T.c_aband + ' (' + pct(S.abandoned, S.total) + ')'],
    [S.gap_count + ' / ' + S.cities.length, fmt(T.c_gaps, {r: D.radius})],
    [pct(S.v6_works, S.connected), T.c_v6]
  ].map(([b, s]) => `<div class="card"><b>${b}</b><span>${s}</span></div>`).join('');

  const life = S.lifespan_median_days == null ? 'n/a' :
    `${S.lifespan_median_days} ${T.days} (~${(S.lifespan_median_days / 365).toFixed(1)} ${T.years}), ${fmt(T.from_n, {n: S.lifespan_n})}`;
  document.getElementById('statusSec').innerHTML = `<h2>${T.h_status}</h2><table><tr><th>${T.th_status}</th><th>${T.th_probes}</th><th>${T.th_share}</th></tr>` +
    D.status_counts.map((n, i) => n ? `<tr><td><span class="dot" style="background:${D.colors[i]}"></span>${T.status[i]}</td><td>${n}</td><td>${pct(n, S.total)}</td></tr>` : '').join('') +
    `</table><p><small>${T.life} ${life}</small></p>`;

  document.getElementById('covSec').innerHTML = `<h2>${T.h_cov}</h2><table><tr><th>${T.th_city}</th><th>${fmt(T.th_within, {r: D.radius})}</th><th>${T.th_near}</th></tr>` +
    S.cities.map(c => `<tr class="${c.gap ? 'gap' : ''}"><td>${cityName(c.city)}</td><td>${c.probes_within}</td><td>${c.nearest_km ?? '-'}</td></tr>`).join('') +
    `</table><p><small>${T.cov_note}</small></p>`;

  document.getElementById('asnSec').innerHTML = `<h2>${T.h_asn}</h2><table><tr><th>ASN</th><th>${T.th_net}</th><th>${T.th_probes}</th></tr>` +
    S.asns.map(([a, n]) => `<tr><td><a href="https://stat.ripe.net/AS${a}" target="_blank">AS${a}</a></td><td>${D.names[a] || ''}</td><td>${n}</td></tr>`).join('') + '</table>';

  document.getElementById('dropSec').innerHTML = `<h2>${T.h_drop}</h2><table><tr><th>${T.th_year}</th><th>${T.th_lost}</th></tr>` +
    S.drop_years.map(([y, n]) => `<tr><td>${y}</td><td>${n}</td></tr>`).join('') + '</table>';

  document.getElementById('lossSec').innerHTML = `<h2>${T.h_loss}</h2><table><tr><th>ASN</th><th>${T.th_net}</th><th>${T.th_probes}</th><th>${T.th_conn}</th><th>${T.th_lostc}</th><th>${T.th_rate}</th></tr>` +
    S.loss.map(([a, t, c, l]) => `<tr><td><a href="https://stat.ripe.net/AS${a}" target="_blank">AS${a}</a></td><td>${D.names[a] || ''}</td><td>${t}</td><td>${c}</td><td>${l}</td><td>${pct(l, t)}</td></tr>`).join('') +
    `</table><p><small>${T.loss_note}</small></p>`;
  document.getElementById('foot').textContent = T.foot;
  document.getElementById('legend').innerHTML = T.status.map((n, i) => D.status_counts[i] ? `<div><span class="dot" style="background:${D.colors[i]}"></span>${n}</div>` : '').join('');
  for (const [p, m] of markers) {
    m.bindPopup(`<b>${T.probe} <a href="https://atlas.ripe.net/probes/${p.id}" target="_blank">#${p.id}</a></b><br>${T.status[p.s]}${p.anchor ? ' (' + T.anchor + ')' : ''}<br>AS${p.asn || '?'} ${p.net}<br>${T.v6}: ${p.v6 ? T.yes : T.no}`);
  }
}
document.querySelectorAll('.lang button').forEach(b => b.onclick = () => render(b.dataset.l));
render('en');
</script></body></html>"""


# ---------------------------------------------------------------- main
def main():
    print("1/3 Loading RIPE Atlas probes for Kazakhstan...")
    try:
        probes = load_probes()
    except Exception as e:
        print(f"\nDownload failed: {e}\nUse the curl command at the top of this file, then run with --from-file probes.json")
        sys.exit(1)
    print(f"    {len(probes)} probes loaded")

    stats = analyse(probes)
    print("2/3 Looking up network names on RIPEstat...")
    names = asn_names({a for a, _ in stats["asns"]} | {r[0] for r in stats["loss"]})

    os.makedirs("site", exist_ok=True)
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(build_page(probes, stats, names))
    with open("site/stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)

    print("3/3 Done! Open site/index.html in your browser.\n")
    print(f"Probes total: {stats['total']} | connected: {stats['connected']} | abandoned: {stats['abandoned']}")
    print(f"Regional centres with no connected probe within {GAP_RADIUS_KM} km: "
          + ", ".join(c["city"] for c in stats["cities"] if c["gap"]))
    print(f"Connected probes with working IPv6: {stats['v6_works']} of {stats['connected']}")


if __name__ == "__main__":
    main()
