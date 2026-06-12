import sys
import re
import shutil
import argparse
from pathlib import Path
from copy import deepcopy

from bs4 import BeautifulSoup
from jinja2 import Template


"""
split_floors.py — Deep Dungeon Compendium floor splitter
----------------------------------------------------------
Splits saved floor-set HTML files into one page per floor.

Usage:
    python split_floors.py "Palace of the Dead 11-20.html"   # single file
    python split_floors.py ddcompendium/potd/                # whole dungeon
    python split_floors.py ddcompendium/                     # everything
    python split_floors.py ddcompendium/ --output my_floors/

Output structure:
    floors/
      index.html            ← You will create this manually
      assets/               ← all images/icons copied here (prefixed to prevent collisions)
      potd/
        floors_001-010/     floor_1.html … floor_10.html
        floors_011-020/     …
      hoh/  eo/  pt/        same pattern

Requirements:  pip install bs4 jinja2
"""


# ── Dungeon metadata ──────────────────────────────────────────────────────────
DUNGEON_KEYS = {
    "potd": "potd",
    "hoh": "hoh",
    "eo": "eo",
    "pt": "pt",
    "palace of the dead": "potd",
    "heaven-on-high": "hoh",
    "eureka orthos": "eo",
    "pagos tunnels": "pt",
}
DUNGEON_LABELS = {
    "potd": "Palace of the Dead",
    "hoh": "Heaven-on-High",
    "eo": "Eureka Orthos",
    "pt": "Pagos Tunnels",
    "unknown": "Deep Dungeon",
}
DUNGEON_MAX = {"potd": 200, "hoh": 100, "eo": 100, "pt": 100}
DUNGEON_SETS = {"potd": 20, "hoh": 10, "eo": 10, "pt": 10}

# ── Warning icon filename → text ──────────────────────────────────────────────
WARNING_TEXT = {
    "explosion": "Enrage / self-destruct",
    "gaze": "Gaze attack",
    "idle_gaze": "Gaze attack (out of combat)",
    "idle": "Out-of-combat ability",
    "movement": "Draw-in / knockback",
    "directional": "Directional AoE",
    "pointblank": "Pointblank AoE",
    "donut": "Donut AoE",
    "warning": "Dangerous ability",
}

# ── Jinja templates ──────────────────────────────────────────────────────────
SIDEBAR_TEMPLATE = Template(
    r"""
<nav class="sidebar">
  <a href="../../index.html" class="home-link">🏠 Home</a>
  {%- for dungeon in dungeons %}
  <details class="dungeon-details"{% if dungeon.is_cur_dung %} open{% endif %}>
    <summary class="dungeon-name">{{ dungeon.name }}</summary>
    <ul class="sets-list">
      {%- for set in dungeon.sets %}
      {%- if set.is_current %}
      <li class="set-node active-set">
        <details open><summary class="set-summary">
          {{ set.start }}–{{ set.end }}</summary><ul class="floors-list">
          {%- for floor in set.floors %}
          <li{% if floor.active %} class="active-floor"{% endif %}><a href="floor_{{ floor.num }}.html">{{ floor.label }}</a></li>
          {%- endfor %}
        </ul></details>
      </li>
      {%- else %}
      <li class="set-node"><a href="{{ set.href }}">{{ set.start }}–{{ set.end }}</a></li>
      {%- endif %}
      {%- endfor %}
    </ul>
  </details>
  {%- endfor %}
</nav>
"""
)

ENEMY_CARD_TEMPLATE = Template(
    r"""
<div class="enemy-card">
  <div class="e-img">{{ img_html }}</div>
  <div class="e-body">
    <h2>{{ name }}{{ badge }}</h2>
    <div class="stats">
      <span><b>Aggro:</b> {{ aggro_html }}</span>
      <span><b>HP:</b> {{ hp }}</span>
      <span><b>Auto-attack:</b> {{ aa }}</span>
    </div>
    {{ warn_section }}
    <div class="vulns"><b>Vulnerabilities:</b> {{ vuln_imgs }}</div>
    <hr class="div">
    {{ ab_html }}
    {{ notes_html }}
  </div>
</div>
"""
)

BOSS_CARD_TEMPLATE = Template(
    r"""
<div class="boss-card">
  <h2>Boss: {{ name }}</h2>
  <div class="boss-layout">
    <div class="b-img">{{ img_html }}</div>
    <div class="b-body">{{ attr_html }}<hr class="div">{{ tbl_html }}{{ notes_html }}</div>
  </div>
  {{ tiles }}
</div>
"""
)

PAGE_TEMPLATE = Template(
    r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Floor {{ floor_num }} — {{ dungeon_label }}</title>
  {{ css }}
</head>
<body>
{{ sidebar }}
<main>
  <div class="top-bar"><a href="../../index.html" class="home-text-link">🏠 Back to Home</a></div>
  <h1>Floor {{ floor_num }}{{ boss_sfx }}</h1>
  <div class="set-label">{{ dungeon_label }} &bull; Floors {{ set_start }}–{{ set_end }}</div>
  <div class="nav-links">{{ prev_link }}{{ next_link }}</div>
{{ content }}
</main>
</body>
</html>
"""
)

PAGE_CSS = """<style>
  :root {
    --bg:       #121212;
    --surface:  #202020;
    --surface2: #2a2a2a;
    --bright:   rgba(255,255,255,0.87);
    --normal:   rgba(255,255,255,0.60);
    --dim:      rgba(255,255,255,0.38);
    --accent:   #F5DA43;
    --border:   #333333;
    --warn-bg:  #1e1500;
    --warn-bdr: #4a3800;
    --warn-txt: #f0d080;
  }
  *, *::before, *::after { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--normal); margin: 0; display: flex;
  }
  a { color: var(--bright); text-decoration: none; }
  a:hover { color: var(--accent); text-decoration: underline; }
  h1, h2, h3 { color: var(--bright); }

  /* ── Sidebar ── */
  .sidebar {
    width: 260px; min-width: 260px;
    background: var(--surface);
    padding: 20px 16px;
    position: sticky; top: 0; height: 100vh; overflow-y: auto;
    border-right: 1px solid var(--border);
    font-size: 0.9em;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .home-link {
    font-size: 1.05em; font-weight: 600; color: var(--bright) !important;
    display: flex; align-items: center; gap: 8px;
    padding: 10px 12px; border-radius: 6px;
    background: var(--surface2);
    transition: background 0.2s, color 0.2s;
    margin-bottom: 12px;
  }
  .home-link:hover {
    background: var(--accent); color: #121212 !important; text-decoration: none;
  }
  .sidebar details { margin: 0; }
  .sidebar summary {
    cursor: pointer; padding: 8px 12px; border-radius: 6px;
    color: var(--bright); font-weight: 500; list-style: none;
    display: flex; align-items: center; gap: 8px;
    transition: background 0.2s;
    user-select: none;
  }
  .sidebar summary:hover { background: var(--surface2); }
  .sidebar summary::-webkit-details-marker { display: none; }
  .sidebar summary::before {
    content: '▶'; display: inline-block;
    font-size: 0.65em; transition: transform 0.2s; color: var(--dim);
    margin-right: 4px;
  }
  .sidebar details[open] > summary::before { transform: rotate(90deg); color: var(--accent); }
  .dungeon-name { font-size: 0.95em; margin-top: 4px; }
  .sets-list, .floors-list { list-style: none; padding: 4px 0 4px 24px; margin: 0; }
  .floors-list { padding-left: 16px; border-left: 1px solid var(--border); margin-left: 12px; }
  .set-summary { font-size: 0.9em; font-weight: 400; color: var(--normal); padding: 6px 12px; }
  .sidebar li { margin: 2px 0; }
  .sidebar a {
    color: var(--normal); display: block; padding: 6px 12px; border-radius: 4px;
    transition: background 0.2s, color 0.2s;
  }
  .sidebar a:hover { background: var(--surface2); color: var(--bright); text-decoration: none; }
  .active-floor > a {
    background: rgba(245, 218, 67, 0.1); color: var(--accent); font-weight: 600;
    border-left: 3px solid var(--accent); padding-left: 9px;
  }

  /* ── Main content ── */
  main { flex: 1; padding: 20px 30px; max-width: 1200px; }
  .top-bar { margin-bottom: 16px; }
  .home-text-link {
    font-size: 0.9em; color: var(--dim); text-decoration: none;
    transition: color 0.2s;
  }
  .home-text-link:hover { color: var(--accent); }
  .set-label { color: var(--dim); font-size: 0.88em; margin-bottom: 14px; }
  .nav-links { display: flex; gap: 20px; margin-bottom: 20px; font-size: 0.95em; }

  /* ── Enemy card ── */
  .enemy-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;
    display: flex; gap: 24px; flex-wrap: wrap;
  }
  .e-img {
    flex: 0 0 380px;
    display: flex; align-items: flex-start; justify-content: center; padding-top: 4px;
  }
  .e-img img { max-width: 360px; max-height: 360px; object-fit: contain; border-radius: 4px; }
  .e-body { flex: 1 1 300px; }
  .e-body h2 {
    margin: 0 0 10px; font-size: 1.2em;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    color: var(--bright);
  }
  .badge {
    display: inline-block; font-size: 0.7em; font-weight: normal;
    padding: 3px 10px; border-radius: 20px; background: #b85000; color: #fff;
  }
  .stats {
    display: flex; flex-wrap: wrap; gap: 16px;
    font-size: 0.95em; margin-bottom: 8px; align-items: center;
  }
  .stats span { display: flex; align-items: center; gap: 6px; }
  .stats b { color: var(--bright); }
  .aggro-text { font-style: italic; color: var(--normal); }
  .vulns { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin: 8px 0; }
  .vulns b { font-size: 0.9em; margin-right: 6px; color: var(--bright); }

  .warnings {
    background: var(--warn-bg); border: 1px solid var(--warn-bdr);
    border-radius: 4px; padding: 6px 12px; margin: 8px 0; font-size: 0.9em;
  }
  .warnings b { color: var(--accent); display: block; margin-bottom: 4px; }
  .w-item { display: inline-flex; align-items: center; gap: 5px; margin-right: 12px; color: var(--warn-txt); }
  .w-item img { vertical-align: middle; flex-shrink: 0; }

  hr.div { border: 0; border-top: 1px solid var(--border); margin: 12px 0; }

  table.ab { border-collapse: collapse; width: 100%; font-size: 0.9em; margin-top: 8px; }
  table.ab th {
    text-align: left; border-bottom: 2px solid var(--border);
    padding: 6px 10px; background: var(--surface2); color: var(--bright);
  }
  table.ab td { padding: 6px 10px; vertical-align: top; border-bottom: 1px solid var(--border); }
  table.ab tr:last-child td { border-bottom: none; }

  .notes { font-size: 0.9em; margin: 8px 0 0; }
  .notes ul { margin: 4px 0; padding-left: 20px; }
  .notes b { color: var(--bright); }

  /* ── Boss card ── */
  .boss-card {
    background: #2a2010; border: 2px solid #b85000;
    border-radius: 8px; padding: 20px; margin-bottom: 24px;
  }
  .boss-card > h2 { margin: 0 0 16px; color: var(--accent); font-size: 1.4em; }
  .boss-layout { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 16px; }
  .b-img { flex: 0 0 380px; text-align: center; }
  .b-img img { max-width: 360px; max-height: 360px; object-fit: contain; border-radius: 4px; }
  .b-body { flex: 1 1 300px; }
  .job-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px; margin-top: 12px;
  }
  .job-tile {
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 6px; padding: 10px 12px; font-size: 0.9em;
  }
  .job-tile .jname { font-weight: bold; margin-bottom: 6px; color: var(--accent); }
  .job-tile ul { margin: 4px 0; padding-left: 18px; }
  .job-tile .ktimes {
    color: var(--normal); margin-top: 8px;
    border-top: 1px solid var(--border); padding-top: 6px;
  }

  .no-data { color: var(--dim); font-style: italic; }

  /* ── Mobile ── */
  @media (max-width: 900px) {
    body { flex-direction: column; }
    .sidebar {
      width: 100%; min-width: unset; height: auto;
      max-height: 45vh; position: relative;
      border-right: none; border-bottom: 1px solid var(--border);
      padding: 10px 14px;
    }
    main { padding: 14px 16px; }
    .e-img, .b-img {
      flex: 0 0 100%; text-align: center; margin-bottom: 12px;
    }
    .e-img img, .b-img img { max-width: 100%; height: auto; }
    table.ab { display: block; overflow-x: auto; }
  }
</style>"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def detect_dungeon_key(path: Path) -> str:
    for part in reversed(path.parts):
        low = part.lower()
        for pat, key in DUNGEON_KEYS.items():
            if low == pat or low.startswith(pat):
                return key
    return "unknown"


def detect_start_floor(source_path: Path, soup: BeautifulSoup) -> int:
    m = re.search(r"(\d+)", source_path.stem)
    if m:
        return int(m.group(1))
    h1 = soup.find("h1")
    if h1:
        m2 = re.search(r"(\d+)", h1.get_text())
        if m2:
            return int(m2.group(1))
    raise ValueError(f"Cannot detect start floor: {source_path.name}")


def assets_folder_name(source_path: Path) -> str:
    return source_path.stem + "_files"


def stem(src: str) -> str:
    """Get filename stem from a path, e.g. 'gaze' from '.../gaze.svg'."""
    return Path(src).stem.lower()


# ── Path fixer ────────────────────────────────────────────────────────────────
def make_fixer(assets_folder: str, prefix: str):
    """
    Returns fix(html_str) that rewrites any src/href pointing into assets_folder
    so it points into ../../assets/<prefix><filename> instead.
    """
    ASSETS_PREFIX = "../../assets/"
    esc = re.escape(assets_folder)
    pat = re.compile(r'(src|href)=(["\'])(' + esc + r'[^"\']*)\2')

    def fix_val(val: str) -> str:
        if val.startswith(("http", "//", "data:", "#", "?")):
            return val
        if val.startswith(assets_folder):
            filename = val.split("/")[-1]
            return ASSETS_PREFIX + prefix + filename
        return val

    def fix(html: str) -> str:
        def sub(m):
            attr, q, val = m.group(1), m.group(2), m.group(3)
            return f"{attr}={q}{fix_val(val)}{q}"

        return pat.sub(sub, html)

    return fix, fix_val


# ── Parsing ───────────────────────────────────────────────────────────────────
def parse_enemies(soup: BeautifulSoup):
    tbody = soup.select_one("table.enemyList tbody")
    if not tbody:
        raise RuntimeError("Enemy table not found.")
    enemies = []
    for idx, tr in enumerate(tbody.find_all("tr", recursive=False)):
        floors_in, patrol = set(), set()
        for td in tr.find_all("td", recursive=False):
            if "floorCell" not in td.get("class", []):
                continue
            if "enemyFloorIn" in td.get("class", []):
                fn = int(td["data-floor"])
                floors_in.add(fn)
                if td.find("img", alt="Patrol"):
                    patrol.add(fn)

        aggro_td = tr.find("td", class_="agroCell") or tr.find("td", class_="aggroCell")
        aggro_img = aggro_td.find("img") if aggro_td else None

        aggro_text = ""
        if aggro_img:
            raw = aggro_img.get("alt", "") or aggro_img.get("title", "")
            aggro_text = re.sub(r"\s*(agro|aggro)\b", "", raw, flags=re.I).strip()

        name_td = tr.find("td", class_="textCell")
        name_txt = name_td.get_text(strip=True) if name_td else ""

        warn_html = ""
        if name_td:
            sib = name_td.find_next_sibling("td")
            if sib and "iconCell" in sib.get("class", []):
                warn_html = sib.decode_contents().strip()

        hp_td = tr.find("td", class_="hpCell")
        aa_td = tr.find("td", class_="attackCell")
        all_icons = tr.find_all("td", class_="iconCell")

        # FIX: The first iconCell is the warning icon. The remaining are vulnerabilities.
        # We skip the first one so warnings don't appear in the vulnerabilities section.
        vuln_cells = all_icons[1:] if len(all_icons) > 1 else []

        enemies.append({
            "floors": floors_in,
            "patrol": patrol,
            "aggro_img": aggro_img,
            "aggro_text": aggro_text,
            "name": name_txt,
            "warn_html": warn_html,
            "hp": hp_td.get_text(strip=True) if hp_td else "",
            "aa": aa_td.get_text(strip=True) if aa_td else "",
            "vuln_cells": [str(td) for td in vuln_cells],
            "gallery_index": idx,
        })
    return enemies


def parse_gallery_items(soup):
    return soup.select("div.galleryItem")


def parse_boss(soup):
    pane = soup.find("div", id="bossPane")
    if not pane:
        return None
    boss_h2 = soup.find("h2", string=re.compile(r"Boss", re.I))
    name = ""
    if boss_h2:
        name = re.sub(r"^Boss\s*:?\s*", "", boss_h2.get_text(strip=True), flags=re.I).strip()
    if not name:
        prev = pane.find_previous_sibling("h2")
        name = prev.get_text(strip=True) if prev else "Boss"

    img_tag = pane.find("img", class_="surfaceImage")
    attr_div = pane.find("div", class_="attributeList")
    tbl = pane.find("table")
    n_h4 = pane.find("h4", string=re.compile(r"^Notes", re.I))
    n_ul = n_h4.find_next_sibling("ul") if n_h4 else None

    jobs = []
    for jd in pane.find_all("div", class_="jobSpecific"):
        classes = jd.get("class", [])
        code = next((c[3:] for c in classes if c.startswith("job") and c != "jobSpecific"), None)
        if not code:
            continue
        notes = []
        ul = jd.find("ul")
        if ul:
            for li in ul.find_all("li"):
                t = li.get_text(strip=True)
                if t and t.lower() != "no notes written":
                    notes.append(t)
        times = []
        kt = jd.find("h4", string=re.compile(r"Kill Time", re.I))
        if kt:
            ku = kt.find_next_sibling("ul")
            if ku:
                for li in ku.find_all("li"):
                    t = li.get_text(strip=True)
                    if t and t.lower() != "no times recorded":
                        times.append(t)
        if notes or times:
            jobs.append({"job": code, "notes": notes, "kill_times": times})

    return {
        "name": name,
        "img_src": img_tag.get("src", "") if img_tag else "",
        "attr_html": str(attr_div) if attr_div else "",
        "tbl_html": str(tbl) if tbl else "",
        "notes_html": str(n_ul) if n_ul else "",
        "jobs": jobs,
    }


# ── Sidebar ───────────────────────────────────────────────────────────────────
def generate_sidebar(dungeon_key, current_set_start, current_floor):
    dungeons = [
        ("potd", "Palace of the Dead", range(1, 201, 10)),
        ("hoh", "Heaven-on-High", range(1, 101, 10)),
        ("eo", "Eureka Orthos", range(1, 101, 10)),
        ("pt", "Pagos Tunnels", range(1, 101, 10)),
    ]

    ctx_dungeons = []
    for d_key, d_name, sets in dungeons:
        is_cur_dung = (d_key == dungeon_key)
        d_sets = []
        for s in sets:
            e = s + 9
            set_folder = f"floors_{s:03d}-{e:03d}"
            is_cur_set = is_cur_dung and (s == current_set_start)
            if is_cur_set:
                floors = []
                for f in range(s, e + 1):
                    lbl = f"Floor {f}" if f != e else f"Floor {f} ☠"
                    floors.append({"num": f, "label": lbl, "active": f == current_floor})
                d_sets.append({
                    "is_current": True,
                    "start": s,
                    "end": e,
                    "floors": floors,
                })
            else:
                if is_cur_dung:
                    href = f"../{set_folder}/floor_{s}.html"
                else:
                    href = f"../../{d_key}/{set_folder}/floor_{s}.html"
                d_sets.append({
                    "is_current": False,
                    "start": s,
                    "end": e,
                    "href": href,
                })
        ctx_dungeons.append({"is_cur_dung": is_cur_dung, "name": d_name, "sets": d_sets})

    return SIDEBAR_TEMPLATE.render(dungeons=ctx_dungeons)


# ── Warning formatter ─────────────────────────────────────────────────────────
def format_warnings(warn_html: str, fix) -> str:
    if not warn_html.strip():
        return ""
    fixed = fix(warn_html)
    soup = BeautifulSoup(fixed, "html.parser")
    parts = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        key = stem(src)
        if "idle_gaze" in src:
            text = WARNING_TEXT["idle_gaze"]
        elif key in WARNING_TEXT:
            text = WARNING_TEXT[key]
        else:
            text = (
                img.get("title", "")
                or img.get("alt", "")
            ).replace(" ability", "").replace("Large or untelegraphed ", "").strip()
        parts.append(f'<span class="w-item">{img} {text}</span>')
    return " ".join(parts)


# ── Card builders ─────────────────────────────────────────────────────────────
def build_enemy_card(enemy, gallery_items, all_imgs, floor_num, fix, fix_val):
    name_txt = enemy["name"]

    # Robustly match the gallery item by name to ensure correct image mapping
    gallery_tag = None
    item_idx = -1
    for i, item in enumerate(gallery_items):
        h3 = item.find("h3")
        if h3 and h3.get_text(strip=True) == name_txt:
            gallery_tag = item
            item_idx = i
            break

    # Fallback to the original index if name matching fails
    if gallery_tag is None and enemy["gallery_index"] < len(gallery_items):
        gallery_tag = gallery_items[enemy["gallery_index"]]
        item_idx = enemy["gallery_index"]

    badge = (
        '<span class="badge">Patrol</span>'
        if floor_num in enemy["patrol"] else ""
    )

    # Aggro icon + text
    aggro_html = ""
    if enemy["aggro_img"]:
        ai = deepcopy(enemy["aggro_img"])
        ai["src"] = fix_val(ai.get("src", ""))
        aggro_html = str(ai)
    if enemy["aggro_text"]:
        aggro_html += f' <span class="aggro-text">{enemy["aggro_text"]}</span>'

    # Vulnerability icons
    vuln_imgs = ""
    for cell in enemy["vuln_cells"]:
        frag = BeautifulSoup(fix(cell), "html.parser")
        img = frag.find("img")
        if img:
            vuln_imgs += str(img)

    # Warnings
    warn_section = ""
    w = format_warnings(enemy["warn_html"], fix)
    if w:
        warn_section = f'<div class="warnings"><b>⚠ Warnings</b>{w}</div>'

    # Ability table
    ab_html = ""
    if gallery_tag:
        tbl = gallery_tag.find("table")
        if tbl:
            ab_html = fix(str(tbl)).replace("<table", '<table class="ab"', 1)

    # Notes
    notes_html = ""
    if gallery_tag:
        h4 = gallery_tag.find("h4", string=re.compile(r"^Notes", re.I))
        if h4:
            ul = h4.find_next_sibling("ul")
            if ul:
                notes_html = f'<div class="notes"><b>Notes:</b>{ul}</div>'

    # Image — pulled from the pre-fetched all_imgs list using the matched index
    img_html = '<span class="no-data">No image</span>'
    if item_idx != -1 and item_idx < len(all_imgs):
        ic = deepcopy(all_imgs[item_idx])
        ic["src"] = fix_val(ic.get("src", ""))
        img_html = str(ic)

    return ENEMY_CARD_TEMPLATE.render(
        name=name_txt,
        badge=badge,
        img_html=img_html,
        aggro_html=aggro_html,
        hp=enemy["hp"],
        aa=enemy["aa"],
        warn_section=warn_section,
        vuln_imgs=vuln_imgs,
        ab_html=ab_html,
        notes_html=notes_html,
    )


def build_boss_card(boss, fix, fix_val):
    if not boss:
        return '<p class="no-data">No boss data found.</p>'

    img_html = f'<img src="{fix_val(boss["img_src"])}" alt="{boss["name"]}">' if boss["img_src"] else ""
    attr_html = fix(boss["attr_html"])
    tbl_html = fix(boss["tbl_html"]).replace("<table", '<table class="ab"', 1) if boss["tbl_html"] else ""
    notes_html = fix(boss["notes_html"])
    notes_html = f'<div class="notes"><b>Notes:</b>{notes_html}</div>' if notes_html else ""

    tiles = ""
    if boss["jobs"]:
        tl = []
        for j in boss["jobs"]:
            nl = "".join(f"<li>{n}</li>" for n in j["notes"])
            nu = f"<ul>{nl}</ul>" if nl else ""
            tl_items = "".join(f"<li>{t}</li>" for t in j["kill_times"])
            tu = (
                f'<div class="ktimes"><b>Kill times:</b><ul>{tl_items}</ul></div>'
                if tl_items else ""
            )
            tl.append(f'<div class="job-tile"><div class="jname">{j["job"]}</div>{nu}{tu}</div>')
        tiles = (
            f'<h3 style="margin:14px 0 8px">Job Notes &amp; Kill Times</h3>'
            f'<div class="job-grid">{"".join(tl)}</div>'
        )

    return BOSS_CARD_TEMPLATE.render(
        name=boss["name"],
        img_html=img_html,
        attr_html=attr_html,
        tbl_html=tbl_html,
        notes_html=notes_html,
        tiles=tiles,
    )


# ── Process one file ──────────────────────────────────────────────────────────
def process_file(source_path: Path, out_root: Path, dungeon_key: str):
    raw = source_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")

    start_floor = detect_start_floor(source_path, soup)
    end_floor = start_floor + 9
    assets_folder = assets_folder_name(source_path)
    dungeon_label = DUNGEON_LABELS.get(dungeon_key, "Deep Dungeon")
    dungeon_max = DUNGEON_MAX.get(dungeon_key, 100)

    enemies = parse_enemies(soup)
    gallery_items = parse_gallery_items(soup)
    boss = parse_boss(soup)

    # Pre-fetch all images for robust mapping
    container = soup.find("div", id="galleryContainer")
    all_imgs = container.select("div.imagePane img.galleryImage") if container else []

    set_folder = f"floors_{start_floor:03d}-{end_floor:03d}"
    out_dir = out_root / dungeon_key / set_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copy all image assets to a single shared folder at the output root
    global_assets = out_root / "assets"
    global_assets.mkdir(exist_ok=True)
    src_assets = source_path.parent / assets_folder

    # Prefix prevents cross-dungeon/cross-set filename collisions
    img_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}
    prefix = f"{dungeon_key}_{start_floor:03d}-{end_floor:03d}_"

    if src_assets.exists():
        for f in src_assets.iterdir():
            if f.is_file() and f.suffix.lower() in img_exts:
                dest = global_assets / (prefix + f.name)
                if not dest.exists():  # don't overwrite duplicates
                    shutil.copy2(f, dest)

    # All asset src/href values rewrite to ../../assets/<prefix><filename>
    fix, fix_val = make_fixer(assets_folder, prefix)

    for floor_num in range(start_floor, end_floor + 1):
        is_boss = (floor_num == end_floor)

        if is_boss:
            content = build_boss_card(boss, fix, fix_val)
            boss_sfx = ' <span style="color:var(--accent);font-size:.7em">(Boss)</span>'
        else:
            fe = [e for e in enemies if floor_num in e["floors"]]
            content = (
                "\n".join(build_enemy_card(e, gallery_items, all_imgs, floor_num, fix, fix_val) for e in fe)
                if fe else '<p class="no-data">No regular enemies on this floor.</p>'
            )
            boss_sfx = ""

        # Nav — seamless cross-set navigation
        if floor_num > start_floor:
            prev_link = f'<a href="floor_{floor_num-1}.html">← Floor {floor_num-1}</a>'
        elif start_floor > 1:
            ps = start_floor - 10
            pe = start_floor - 1
            prev_link = f'<a href="../floors_{ps:03d}-{pe:03d}/floor_{pe}.html">← Floor {pe}</a>'
        else:
            prev_link = ""

        if floor_num < end_floor:
            next_link = f'<a href="floor_{floor_num+1}.html">Floor {floor_num+1} →</a>'
        elif end_floor < dungeon_max:
            ns = end_floor + 1
            ne = end_floor + 10
            next_link = f'<a href="../floors_{ns:03d}-{ne:03d}/floor_{ns}.html">Floor {ns} →</a>'
        else:
            next_link = ""

        sidebar = generate_sidebar(dungeon_key, start_floor, floor_num)

        page = PAGE_TEMPLATE.render(
            floor_num=floor_num,
            dungeon_label=dungeon_label,
            set_start=start_floor,
            set_end=end_floor,
            css=PAGE_CSS,
            sidebar=sidebar,
            boss_sfx=boss_sfx,
            prev_link=prev_link,
            next_link=next_link,
            content=content,
        )
        (out_dir / f"floor_{floor_num}.html").write_text(page, encoding="utf-8")

    print(
        f"  [{start_floor:3d}–{end_floor:3d}] {len(enemies):2d} enemies  "
        f"boss: {boss['name'] if boss else 'none'}  →  {dungeon_key}/{set_folder}/"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────
def collect_html_files(targets):
    result, seen = [], set()
    for t in targets:
        p = Path(t).resolve()
        files = sorted(p.rglob("*.html")) if p.is_dir() else ([p] if p.suffix.lower() == ".html" else [])
        for f in files:
            if f not in seen:
                seen.add(f)
                result.append(f)
    return result


def main():
    ap = argparse.ArgumentParser(description="Split DD Compendium floor-set HTMLs into per-floor pages.")
    ap.add_argument("inputs", nargs="+", help="HTML file(s) or folder(s)")
    ap.add_argument("--output", "-o", default="floors", help="Output root (default: ./floors)")
    args = ap.parse_args()

    out_root = Path(args.output).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    html_files = collect_html_files(args.inputs)
    if not html_files:
        print("No HTML files found.")
        sys.exit(1)

    print(f"Processing {len(html_files)} file(s) → {out_root}\n")
    errors = []
    for hp in html_files:
        dk = detect_dungeon_key(hp)
        try:
            process_file(hp, out_root, dk)
        except Exception as e:
            import traceback
            traceback.print_exc()
            errors.append((hp, e))

    print(f"\nDone. {len(html_files)-len(errors)} OK, {len(errors)} failed.")
    if errors:
        for p, e in errors:
            print(f"  ✗ {p.name}: {e}")
    print(f"\nOutput: {out_root}")
    print("  index.html        ← Create your home page here")
    print("  assets/           ← all images/icons (shared, prefixed)")
    print("  potd/floors_NNN-MMM/floor_N.html")
    print("  hoh/  eo/  pt/    (same structure)")


if __name__ == "__main__":
    main()