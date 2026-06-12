"""
scrape_ddcompendium.py
----------------------
Downloads every floor-set page from ddcompendium.com as a "Save Page Complete"
equivalent — the HTML file plus its assets folder — just like Ctrl+S in a browser.

Usage:
    python scrape_ddcompendium.py                    # download everything
    python scrape_ddcompendium.py --dungeon potd     # one dungeon only
    python scrape_ddcompendium.py --dungeon hoh eo   # multiple dungeons
    python scrape_ddcompendium.py --delay 3          # seconds between pages (default 2)
    python scrape_ddcompendium.py --output ./saved   # where to save (default ./ddcompendium)

Dungeons available: potd, hoh, eo, pt

Requirements:  pip install bs4 requests
"""

import argparse
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Site map
# ---------------------------------------------------------------------------

BASE_URL = 'https://www.ddcompendium.com'

DUNGEONS = {
    'potd': {'name': 'Palace of the Dead', 'path': 'potd_floorsets', 'sets': range(1, 201, 10)},
    'hoh':  {'name': 'Heaven-on-High',     'path': 'hoh_floorsets',  'sets': range(1, 101, 10)},
    'eo':   {'name': "Eureka Orthos",      'path': 'eo_floorsets',   'sets': range(1, 101, 10)},
    'pt':   {'name': 'Pagos Tunnels',      'path': 'pt_floorsets',   'sets': range(1, 101, 10)},
}

# Assets that are shared across all pages (fetched from the base site)
COMMON_ASSET_PATHS = [
    '/assets/css/style.css',
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def floor_set_url(dungeon_path: str, start_floor: int) -> str:
    """e.g. potd_floorsets, 61 -> https://www.ddcompendium.com/potd_floorsets/061.html"""
    return f'{BASE_URL}/{dungeon_path}/{start_floor:03d}.html'


def safe_filename(url: str) -> str:
    """Convert a URL path component to a safe local filename."""
    parsed = urlparse(url)
    name = parsed.path.split('/')[-1]
    return name if name else 'index.html'


def page_stem(dungeon_key: str, start_floor: int) -> str:
    """Returns the stem used for the saved HTML file and its _files folder."""
    dname = DUNGEONS[dungeon_key]['name']
    end_floor = start_floor + 9
    return f'{dname} {start_floor}-{end_floor}'


def get_with_retry(session: requests.Session, url: str, retries=3, delay=5):
    """GET with simple retry logic."""
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"    Retry {attempt+1}/{retries-1} for {url}: {e}")
                time.sleep(delay)
            else:
                raise


# ---------------------------------------------------------------------------
# Asset discovery and download
# ---------------------------------------------------------------------------

def collect_asset_urls(soup: BeautifulSoup, page_url: str) -> set:
    """
    Find all local asset URLs referenced in the HTML:
    images, stylesheets, scripts (but not external CDN links).
    """
    assets = set()

    # img src
    for tag in soup.find_all('img', src=True):
        src = tag['src']
        if not src.startswith(('http', 'data:', '//')):
            assets.add(urljoin(page_url, src))

    # link href (CSS)
    for tag in soup.find_all('link', href=True):
        href = tag['href']
        if not href.startswith(('http', '//', 'data:')):
            assets.add(urljoin(page_url, href))
        elif urlparse(href).netloc == 'www.ddcompendium.com':
            assets.add(href)

    # script src
    for tag in soup.find_all('script', src=True):
        src = tag['src']
        if not src.startswith(('http', '//')):
            assets.add(urljoin(page_url, src))
        elif urlparse(src).netloc == 'www.ddcompendium.com':
            assets.add(src)

    # Filter to same-origin only
    assets = {a for a in assets if urlparse(a).netloc in ('www.ddcompendium.com', '')}
    return assets


def download_asset(session: requests.Session, url: str, dest_path: Path):
    """Download a single asset to dest_path, skip if already present."""
    if dest_path.exists():
        return  # already downloaded
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = get_with_retry(session, url)
        dest_path.write_bytes(r.content)
    except Exception as e:
        print(f"    [WARN] Could not download asset {url}: {e}")


def rewrite_html_paths(html: str, assets_folder_name: str) -> str:
    """
    Rewrite absolute ddcompendium.com asset URLs to relative paths
    pointing into the _files folder, matching what Ctrl+S produces.
    Also rewrite root-relative paths (/assets/...) the same way.
    """
    def replacer(m):
        attr  = m.group(1)
        quote = m.group(2)
        val   = m.group(3)

        # Already relative and already in _files folder: leave it
        if val.startswith(assets_folder_name):
            return m.group(0)

        parsed = urlparse(val)

        # Absolute URL on ddcompendium.com
        if parsed.netloc in ('www.ddcompendium.com', ''):
            filename = parsed.path.split('/')[-1]
            if filename:
                new_val = f'{assets_folder_name}/{filename}'
                return f'{attr}={quote}{new_val}{quote}'

        return m.group(0)

    pattern = re.compile(r'(src|href)=(["\'])([^"\']+)\2')
    return pattern.sub(replacer, html)


# ---------------------------------------------------------------------------
# Single page saver
# ---------------------------------------------------------------------------

def save_page(session: requests.Session, dungeon_key: str,
              start_floor: int, out_dir: Path, delay: float):
    """Download one floor-set page and all its assets."""
    url  = floor_set_url(DUNGEONS[dungeon_key]['path'], start_floor)
    stem = page_stem(dungeon_key, start_floor)
    html_path   = out_dir / f'{stem}.html'
    assets_dir  = out_dir / f'{stem}_files'
    assets_name = f'{stem}_files'

    if html_path.exists():
        print(f"  [SKIP] {html_path.name} already exists")
        return

    print(f"  Fetching {url} ...", end='', flush=True)
    try:
        r = get_with_retry(session, url)
    except Exception as e:
        print(f"\n  [ERROR] {e}")
        return

    # Give the page a moment (politeness + simulates full load wait)
    time.sleep(delay)
    print(f" {r.status_code} ({len(r.content)//1024} KB)")

    soup = BeautifulSoup(r.text, 'html.parser')
    asset_urls = collect_asset_urls(soup, url)

    # Download each asset into the _files folder
    for asset_url in sorted(asset_urls):
        filename = urlparse(asset_url).path.split('/')[-1]
        if not filename:
            continue
        dest = assets_dir / filename
        download_asset(session, asset_url, dest)

    # Rewrite HTML so all references are local
    local_html = rewrite_html_paths(r.text, assets_name)

    html_path.write_text(local_html, encoding='utf-8')
    print(f"    Saved: {html_path.name}  +  {len(asset_urls)} assets")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Scrape ddcompendium.com floor sets')
    parser.add_argument('--dungeon', nargs='+', choices=list(DUNGEONS.keys()),
                        default=list(DUNGEONS.keys()),
                        help='Which dungeon(s) to download (default: all)')
    parser.add_argument('--delay',  type=float, default=2.0,
                        help='Seconds to wait between page downloads (default: 2)')
    parser.add_argument('--output', type=str, default='./ddcompendium',
                        help='Output folder (default: ./ddcompendium)')
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    total_pages = sum(
        len(list(DUNGEONS[d]['sets'])) for d in args.dungeon
    )
    print(f"Downloading {total_pages} floor sets into {out_dir.resolve()}")
    print(f"Delay between pages: {args.delay}s\n")

    done = 0
    for dungeon_key in args.dungeon:
        d = DUNGEONS[dungeon_key]
        dname = d['name']
        dungeon_dir = out_dir / dungeon_key
        dungeon_dir.mkdir(exist_ok=True)
        print(f"\n=== {dname} ({dungeon_key.upper()}) ===")

        for start_floor in d['sets']:
            done += 1
            print(f"[{done}/{total_pages}] Floors {start_floor}-{start_floor+9}")
            save_page(session, dungeon_key, start_floor, dungeon_dir, args.delay)

    print(f"\nAll done. Files saved to: {out_dir.resolve()}")
    print("You can now run:  python split_floors.py <path-to-any-saved-html>")


if __name__ == '__main__':
    main()