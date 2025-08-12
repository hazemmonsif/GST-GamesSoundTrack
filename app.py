import os
import json
import threading
import time
import random
import re
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, Response

def default_download_dir():
    # Android? Use public Downloads
    if 'ANDROID_ARGUMENT' in os.environ:
        try:
            from android.storage import primary_external_storage_path
            base_root = Path(primary_external_storage_path())
        except Exception:
            base_root = Path('/sdcard')
        base = base_root / 'Download' / 'GameSoundtracks'
    else:
        base = Path.home() / "Downloads" / "GameSoundtracks"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)

def normalize_output_path(user_value=None):
    if not user_value or not str(user_value).strip():
        return default_download_dir()
    p = Path(user_value)
    if not p.is_absolute():
        p = Path(default_download_dir()) / p
    try:
        p.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        p = Path(default_download_dir())
    return str(p.resolve())

# -------------------------- Backend ---------------------------------

app = Flask(__name__)

download_progress = {}
active_downloads = {}

class FixedKHInsiderDownloader:
    """KHInsider downloader with working search, track selection, and stream resolver."""
    def __init__(self):
        self.base_url = "https://downloads.khinsider.com"
        self.session = requests.Session()
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        self._setup_session()

    def _setup_session(self):
        self.session.headers.update({
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

    def _get_with_retry(self, url, max_retries=3):
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    time.sleep(random.uniform(1, 3))
                    self.session.headers['User-Agent'] = random.choice(self.user_agents)
                resp = self.session.get(url, timeout=20)
                if resp.status_code == 403 and attempt < max_retries - 1:
                    time.sleep(random.uniform(2, 5))
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException:
                if attempt == max_retries - 1:
                    raise
        return None

    def search(self, query):
        try:
            search_url = f"{self.base_url}/search"
            params = {'search': query}
            resp = self._get_with_retry(search_url + "?" + urllib.parse.urlencode(params))
            if not resp:
                return []
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []

            table = soup.find('table', {'id': 'albumlist'}) \
                 or soup.find('table', {'class': 'albumlist'}) \
                 or soup.find('table', {'class': 'chart'})
            if table:
                for row in table.find_all('tr'):
                    if row.find('th'):
                        continue
                    link = row.find('a', href=lambda x: x and '/game-soundtracks/album/' in x)
                    if not link:
                        continue
                    album_url = link.get('href')
                    if album_url.startswith('/'):
                        album_url = self.base_url + album_url
                    album_id = album_url.split('/album/')[-1]
                    name = link.get_text(strip=True)
                    icon = None
                    img = row.find('img')
                    if img and img.get('src'):
                        src = img.get('src')
                        icon = (self.base_url + src) if src.startswith('/') else src
                    if name:
                        results.append({'id': album_id, 'name': name, 'url': album_url, 'icon': icon})

            if not results:
                for link in soup.find_all('a', href=lambda x: x and '/game-soundtracks/album/' in x):
                    album_url = link.get('href')
                    if album_url.startswith('/'):
                        album_url = self.base_url + album_url
                    album_id = album_url.split('/album/')[-1]
                    name = link.get_text(strip=True)
                    icon = None
                    parent = link.parent
                    if parent:
                        img = parent.find('img')
                        if img and img.get('src'):
                            src = img.get('src')
                            icon = (self.base_url + src) if src.startswith('/') else src
                    if name and len(name) > 2:
                        results.append({'id': album_id, 'name': name, 'url': album_url, 'icon': icon})

            return results[:20]
        except Exception:
            return []
    def get_home_sections(self, max_items=24):
        """Scrape KHInsider homepage for Popular Series and Latest Soundtracks."""
        try:
            resp = self._get_with_retry(self.base_url + "/")
            if not resp:
                return {"popular": [], "latest": []}
            soup = BeautifulSoup(resp.content, "html.parser")
            popular = self._extract_popular_series(soup, max_items)
            latest = self._extract_home_section(soup, ("latest soundtracks", "latest", "newest"), max_items)
            return {"popular": popular, "latest": latest}
        except Exception:
            return {"popular": [], "latest": []}
    def _extract_popular_series(self, soup, max_items):
      """
      Extract the Popular Series from the exact homepage block:
        <div id="homepagePopularSeries"><ul><li><a href="/mario" class="mainlevel">Mario</a>...
      """
      out = []
      host = self.base_url.rstrip("/")
      container = soup.find(id="homepagePopularSeries")
      if not container:
          return out

      for a in container.find_all("a", class_="mainlevel", href=True):
          name = a.get_text(strip=True)
          href = a["href"].strip()
          url = f"{host}{href}" if href.startswith("/") else href
          slug = href.strip("/").split("/")[0] if href else None
          if not name or not slug:
              continue
          # type=series lets the frontend render these differently from albums
          out.append({"id": slug, "name": name, "url": url, "type": "series", "icon": None})
          if len(out) >= max_items:
              break
      return out

    def _extract_home_section(self, soup, heading_keywords, max_items):
        """
        Find links to albums that appear between a section heading (h2/h3) that matches
        any keyword and the next h2/h3. Falls back to scanning the whole page.
        """
        def make_item(a):
            href = a.get("href", "")
            if href.startswith("/"):
                href = self.base_url + href
            if "/game-soundtracks/album/" not in href:
                return None
            album_id = href.split("/album/")[-1]
            name = a.get_text(strip=True)
            # try to find an img near the link
            img = a.find("img")
            icon = None
            if img and img.get("src"):
                src = img.get("src")
                icon = (self.base_url + src) if src.startswith("/") else src
            else:
                # look up a nearby image
                parent_img = a.parent.find("img") if a.parent else None
                if parent_img and parent_img.get("src"):
                    src = parent_img.get("src")
                    icon = (self.base_url + src) if src.startswith("/") else src
            if not name:
                return None
            return {"id": album_id, "name": name, "url": href, "icon": icon}

        # 1) try bounded-by-heading section
        h = None
        for tag in soup.find_all(["h2", "h3"]):
            text = tag.get_text(" ", strip=True).lower()
            if any(k in text for k in heading_keywords):
                h = tag
                break

        items = []
        if h:
            for sib in h.next_siblings:
                # stop at next section heading
                if getattr(sib, "name", None) in ("h2", "h3"):
                    break
                find_all = getattr(sib, "find_all", None)
                if callable(find_all):
                    for a in find_all("a", href=True):
                        it = make_item(a)
                        if it:
                            items.append(it)
                            if len(items) >= max_items:
                                break
                if len(items) >= max_items:
                    break

        # 2) fallback: scan entire page for album links
        if not items:
            for a in soup.find_all("a", href=True):
                it = make_item(a)
                if it:
                    items.append(it)
                    if len(items) >= max_items:
                        break

        # dedupe by id, keep order
        seen, out = set(), []
        for it in items:
            if it["id"] not in seen:
                seen.add(it["id"])
                out.append(it)
        return out[:max_items]

    def get_soundtrack_info(self, soundtrack_id):
        try:
            if soundtrack_id.startswith('http'):
                url = soundtrack_id
                album_id = soundtrack_id.split('/album/')[-1] if '/album/' in soundtrack_id else soundtrack_id
            else:
                url = f"{self.base_url}/game-soundtracks/album/{soundtrack_id}"
                album_id = soundtrack_id

            resp = self._get_with_retry(url)
            if not resp:
                return None
            soup = BeautifulSoup(resp.content, 'html.parser')

            title = self._extract_title(soup, album_id)
            icon = self._extract_album_icon(soup)
            tracks = self._extract_tracks_from_table(soup)

            return {'id': album_id, 'title': title, 'icon': icon, 'tracks': tracks, 'total_tracks': len(tracks)}
        except Exception:
            return None

    def _extract_title(self, soup, fallback_id):
        if soup.title:
            title = soup.title.get_text().strip()
            if title and "403" not in title and "error" not in title.lower():
                title = re.sub(r'\s*-\s*Download.*$', '', title, flags=re.I)
                title = re.sub(r'\s*-\s*KHInsider.*$', '', title, flags=re.I)
                title = re.sub(r'\s*MP3.*$', '', title, flags=re.I)
                title = re.sub(r'\s*\([^)]*download[^)]*\)', '', title, flags=re.I)
                return title.strip()
        for tag in ['h1', 'h2', 'h3']:
            el = soup.find(tag)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) > 3:
                    return text
        return fallback_id.replace('-', ' ').title()

    def _extract_album_icon(self, soup):
        for img in soup.find_all('img'):
            src = img.get('src', '')
            alt = img.get('alt', '').lower()
            if any(k in src.lower() for k in ['album', 'cover', 'artwork', 'thumb']) or \
               any(k in alt for k in ['album', 'cover', 'artwork']):
                if src.startswith('/'):
                    return self.base_url + src
                if src.startswith('http'):
                    return src
        return None

    def _extract_tracks_from_table(self, soup):
        tracks = []
        table = soup.find('table', id='songlist')
        if not table:
            return tracks
        rows = table.find_all('tr')
        n = 1
        for row in rows:
            if row.get('id') in ['songlist_header', 'songlist_footer']:
                continue
            cell = row.find('td', class_='clickable-row')
            if not cell:
                continue
            link = cell.find('a', href=True)
            if not link:
                continue
            name = link.get_text(strip=True)
            href = link.get('href')
            if href.startswith('/'):
                href = self.base_url + href
            if name and href:
                tracks.append({'number': n, 'name': name, 'url': href})
                n += 1
        return tracks

    def get_download_link(self, track_page_url):
        try:
            resp = self._get_with_retry(track_page_url)
            if not resp:
                return None
            soup = BeautifulSoup(resp.content, 'html.parser')

            audio = soup.find('audio')
            if audio and audio.get('src'):
                return audio.get('src')

            for a in soup.find_all('a', class_='songDownloadLink', href=True):
                return a.get('href')

            for a in soup.find_all('a', href=True):
                h = a.get('href')
                if h and 'vgmsite.com' in h:
                    return h

            for a in soup.find_all('a', href=True):
                h = a.get('href', '').lower()
                if any(ext in h for ext in ['.mp3', '.flac', '.ogg', '.wav']):
                    return a.get('href')

            return None
        except Exception:
            return None

    def download_track(self, track_url, output_dir, filename):
        try:
            dl = self.get_download_link(track_url)
            if not dl:
                return False
            resp = self._get_with_retry(dl)
            if not resp:
                return False
            if not any(filename.endswith(ext) for ext in ['.mp3', '.flac', '.ogg', '.wav']):
                filename += '.mp3'
            os.makedirs(output_dir, exist_ok=True)
            fpath = os.path.join(output_dir, filename)
            with open(fpath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return os.path.getsize(fpath) > 1000
        except Exception:
            return False

    def download_soundtrack(self, album_id, output_path='./downloads', selected_tracks=None, progress_id=None):
        try:
            if progress_id:
                download_progress[progress_id] = {
                    'status': 'starting', 'current_track': 0, 'total_tracks': 0,
                    'current_file': '', 'message': 'Getting album information...'
                }

            info = self.get_soundtrack_info(album_id)
            if not info:
                if progress_id:
                    download_progress[progress_id].update({'status': 'error', 'message': f'Album not found: {album_id}'})
                return False

            all_tracks = info['tracks']
            tracks = [t for t in all_tracks if (not selected_tracks) or (t['name'] in selected_tracks)]
            total = len(tracks)
            if total == 0:
                if progress_id:
                    download_progress[progress_id].update({'status': 'error', 'message': 'No tracks to download'})
                return False

            if progress_id:
                download_progress[progress_id].update({
                    'total_tracks': total,
                    'status': 'downloading',
                    'message': f'Downloading {total} tracks from "{info["title"]}"'
                })

            safe_title = "".join(c for c in info['title'] if c.isalnum() or c in (' ', '-', '_', '.')).strip()
            album_dir = os.path.join(output_path, safe_title)
            os.makedirs(album_dir, exist_ok=True)

            done = 0
            for i, track in enumerate(tracks, 1):
                if progress_id and progress_id not in active_downloads:
                    break
                if progress_id:
                    download_progress[progress_id].update({
                        'current_track': i,
                        'current_file': track['name'],
                        'message': f'Downloading: {track["name"]} ({i}/{total})'
                    })
                safe_name = "".join(c for c in track['name'] if c.isalnum() or c in (' ', '-', '_', '.')).strip()
                fname = f"{track['number']:02d} - {safe_name}"
                if self.download_track(track['url'], album_dir, fname):
                    done += 1
                time.sleep(random.uniform(1, 2.2))

            if progress_id:
                if progress_id in active_downloads:
                    download_progress[progress_id].update({
                        'status': 'completed', 'current_track': done,
                        'message': f'Downloaded {done}/{total} tracks to: {album_dir}'
                    })
                else:
                    download_progress[progress_id].update({'status': 'cancelled', 'message': 'Download cancelled'})
            return done > 0
        except Exception as e:
            if progress_id:
                download_progress[progress_id].update({'status': 'error', 'message': f'Download failed: {e}'})
            return False

downloader = FixedKHInsiderDownloader()

# -------------------------- Frontend (HTML) --------------------------

FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Games Soundtrack Downloader</title>
  <style>
    @import "https://unpkg.com/open-props/easings.min.css";

    :root{
      --bg1:#10131a; --bg2:#0c0f15; --card:#161b23;
      --muted:#8792a2; --text:#e6ebf3;
      --brand:#7cc8ff; --brand-2:#a8d1ff; --accent:#66e2ff;
      --danger:#ff5c7c;
      --shadow:0 20px 40px rgba(0,0,0,.35);
      --radius:16px; --radius-lg:22px;
      --ring:0 0 0 3px rgba(124,140,255,.15);
      --ring-strong:0 0 0 4px rgba(124,140,255,.28);
      --speed:280ms;
      --grain-opacity:.035; --grain-size:180px 180px; --grain-speed:2500ms;
      --icon-fill: var(--muted); --icon-fill-hover: var(--brand);
      --chip:#1b2333; --chip-border:#2a3550;
    }
    [data-theme="light"]{
      --bg1:#f6f8fc; --bg2:#eef2f9; --card:#ffffff;
      --muted:#5b6578; --text:#0d1220;
      --ring:0 0 0 3px rgba(124,140,255,.18);
      --ring-strong:0 0 0 4px rgba(124,140,255,.28);
      --shadow:0 10px 30px rgba(12,16,28,.08);
      --chip:#eef2f9; --chip-border:#dee5f2;
    }
    [data-theme="light"] .panel,
    [data-theme="light"] .track-list,
    [data-theme="light"] .card{ border-color:#e4e9f5; }
    [data-theme="light"] .card{
      background: linear-gradient(180deg, #ffffff, #f6f8fc);
      box-shadow: 0 8px 20px rgba(12,16,28,.06);
    }
    [data-theme="light"] .pill{ background:#eef2f9; border-color:#dee5f2; }
    [data-theme="light"] .track{ border-bottom:1px solid #edf1f8; }
    [data-theme="light"] .download{ color:#0d1220; }

    /* Theme toggle visuals */
    .sun-and-moon > :is(.moon, .sun, .sun-beams){ transform-origin:center; }
    .sun-and-moon > :is(.moon, .sun){ fill: var(--icon-fill); }
    .theme-toggle:is(:hover, :focus-visible) > .sun-and-moon > :is(.moon, .sun){ fill: var(--icon-fill-hover); }
    .sun-and-moon > .sun-beams{ stroke: var(--icon-fill); stroke-width:2px; }
    .theme-toggle:is(:hover, :focus-visible) .sun-and-moon > .sun-beams{ stroke: var(--icon-fill-hover); }
    [data-theme="dark"] .sun-and-moon > .sun{ transform: scale(1.75); }
    [data-theme="dark"] .sun-and-moon > .sun-beams{ opacity:0; }
    [data-theme="dark"] .sun-and-moon > .moon > circle{ transform: translateX(-7px); }
    @supports (cx: 1){
      [data-theme="dark"] .sun-and-moon > .moon > circle{ cx:17; transform: translateX(0); }
    }
    @media (prefers-reduced-motion:no-preference){
      .sun-and-moon > .sun{ transition: transform .5s var(--ease-elastic-3); }
      .sun-and-moon > .sun-beams{ transition: transform .5s var(--ease-elastic-4), opacity .5s var(--ease-3); }
      .sun-and-moon .moon > circle{ transition: transform .25s var(--ease-out-5); }
      @supports (cx:1){ .sun-and-moon .moon > circle{ transition: cx .25s var(--ease-out-5); } }
      [data-theme="dark"] .sun-and-moon > .sun{ transition-timing-function:var(--ease-3); transition-duration:.25s; transform:scale(1.75); }
      [data-theme="dark"] .sun-and-moon > .sun-beams{ transition-duration:.15s; transform:rotateZ(-25deg); }
      [data-theme="dark"] .sun-and-moon > .moon > circle{ transition-duration:.5s; transition-delay:.25s; }
    }

    *{box-sizing:border-box;margin:0;padding:0}
    html,body{height:100%}
    body{
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
      color:var(--text);
      background: radial-gradient(1500px 800px at 10% -10%, #26304a55 0%, transparent 60%),
                  radial-gradient(1200px 900px at 120% -20%, #3c275a55 0%, transparent 60%),
                  linear-gradient(135deg, var(--bg1), var(--bg2));
      background-attachment: fixed;
      -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale;
      padding: 32px;
    }
    body:before{
      content:""; position:fixed; inset:-20%;
      background: conic-gradient(from 180deg at 50% 50%,
                  transparent 0 30deg, rgba(255,255,255,.04) 90deg, transparent 140deg);
      filter: blur(65px);
      animation: rotateSheen 56s linear infinite; pointer-events:none;
    }
    @keyframes rotateSheen { to{ transform: rotate(1turn); } }

    .app{ max-width: 1200px; margin:0 auto; display:grid; gap:18px; grid-template-rows: auto auto 1fr auto; }

    .header{
      background: linear-gradient(180deg, rgba(124,140,255,.12), rgba(124,140,255,0) 60%);
      border:1px solid rgba(124,140,255,.18);
      border-radius: var(--radius-lg);
      padding: 22px 22px;
      box-shadow: var(--shadow);
      display:flex; align-items:center; gap:14px; position:relative;
    }
    /* Header responsiveness */
    @media (max-width: 720px){
      .header{ flex-wrap: wrap; padding:16px; gap:10px; }
      .logo{ width:40px; height:40px; }
      .title-wrap{ min-width:0; flex:1 1 100%; }
      .subtitle{ font-size:12px; }
      /* let buttons participate in flow on small screens */
      #settingsBtn, #theme-toggle{ position:static; }
    }

    /* Footer player responsiveness */
    @media (max-width: 820px){
      .player{ grid-template-columns: 1fr; gap:8px; padding:10px; }
      .controls{ flex-wrap: wrap; justify-content:flex-start; }
      .player .right{ grid-template-columns: 1fr; gap:6px; min-width:0; }
      .player .time{ text-align:left; }
      #seek{ width:100%; }
      #vol{ width:100%; }
    }

    /* Track row now has a star column */
    .track{ grid-template-columns: 24px 32px 28px 1fr auto; }

    /* Star button look */
    .favbtn{
      width:28px;height:28px;border-radius:8px;display:grid;place-items:center;cursor:pointer;
      color:#ffd166; user-select:none;
    }
    .favbtn:hover{ filter: brightness(1.1); }
    .favbtn.on{ filter: drop-shadow(0 0 6px rgba(255,209,102,.35)); }


    /* Player */
    .player{
      position: fixed; left: 32px; right: 32px; bottom: 24px;
      display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items:center;
      background: var(--card); border:1px solid #212938;
      border-radius: 16px; padding: 10px 12px; box-shadow: var(--shadow); z-index: 50;
    }
    .player .track-meta{ display:flex; align-items:center; gap:10px; min-width:0; }
    .player .cover{ width:44px;height:44px;border-radius:10px; overflow:hidden; border:1px solid #263145; flex-shrink:0; background:#0f131b; }
    .player .cover img{ width:100%; height:100%; object-fit:cover; display:block; }
    .player .meta{ min-width:0; }
    .player .meta .title{ font-weight:800; font-size:14px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .player .meta .subtitle{ font-size:12px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .player .controls{ display:flex; align-items:center; gap:8px; justify-content:center; }
    .ctrl-btn{
      background:#151b28; border:1px solid #2a3550; border-radius:10px; padding:8px 10px;
      color:var(--text); cursor:pointer; min-width:42px; display:grid; place-items:center;
    }
    .ctrl-btn:hover{ border-color: var(--brand); }
    [data-theme="light"] .ctrl-btn{ background:#eef2f9; border-color:#dee5f2; }
    .ctrl-btn svg{ display:block; }
    .ctrl-btn.active{ border-color: var(--brand); box-shadow: 0 0 0 2px rgba(124,140,255,.22); }

    .player .right{
      display:grid; grid-template-columns:auto 1fr auto auto;
      gap:10px; align-items:center; min-width:280px;
    }
    .player .time{ font-size:12px; color:var(--muted); min-width:110px; text-align:right; }
    #seek { width: clamp(160px, 36vw, 420px); accent-color: var(--brand); }
    #vol  { width: 120px; accent-color: var(--brand); }

    .logo{ width:44px;height:44px;border-radius:12px;
      background: linear-gradient(135deg, var(--brand), var(--brand-2));
      display:grid;place-items:center; box-shadow:0 8px 30px rgba(124,140,255,.35);
    }
    .logo span{font-size:22px;filter: drop-shadow(0 1px 0 rgba(0,0,0,.35))}
    .title-wrap{display:flex;flex-direction:column;}
    h1{ font-size: clamp(20px, 3vw, 26px); line-height:1.2; letter-spacing:.2px; font-weight:750; }
    .subtitle{color:var(--muted);font-size:14px;}

    .icon-btn, .theme-toggle{
      background:#151b28;border:1px solid #2a3550;border-radius:10px;
      padding:6px 8px;color:var(--text);display:grid;place-items:center;cursor:pointer;
      position:absolute; top:12px;
    }
    .icon-btn:hover, .theme-toggle:hover{border-color:var(--brand)}
    #settingsBtn{ right:12px; }
    #theme-toggle{ right:56px; }
    [data-theme="light"] .theme-toggle{ background:#eef2f9; border-color:#dee5f2; }

    .sheet{position:fixed; inset:0; background:rgba(0,0,0,.4); display:grid; place-items:end; z-index:60}
    .sheet[hidden]{display:none}
    .sheet-inner{background:#0f141d;border:1px solid #253148;width:min(420px,92vw);border-radius:16px 16px 0 0;padding:16px;box-shadow:var(--shadow)}
    .sheet-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
    .sheet-body .row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0}

    .panel{ background: var(--card); border:1px solid #212938; border-radius: var(--radius); padding:16px; box-shadow: var(--shadow); }
    .panel h3{font-size:16px;margin-bottom:10px;font-weight:750;letter-spacing:.2px}

    .status{ display:none; padding:12px 14px; border-radius:12px; font-size:14px;
      background: rgba(75,225,160,.12); border:1px solid rgba(75,225,160,.35); color:#bff0d9; box-shadow:var(--shadow); animation: fadeSlide .5s ease; }
    .status.error{ background: rgba(255,92,124,.12); border-color: rgba(255,92,124,.35); color:#ffd0db; }
    @keyframes fadeSlide{ from{ opacity:0; transform: translateY(6px) } to{ opacity:1; transform: translateY(0) } }

    .search-row{ display:grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; }
    .input{ background: var(--card); border:1px solid #212938; padding: 14px 16px; border-radius: 14px; color:var(--text);
      outline:none; box-shadow: var(--ring); transition: box-shadow var(--speed), border-color var(--speed), transform var(--speed); width:100%; }
    .input:focus{ border-color: var(--brand); box-shadow: var(--ring-strong); transform: translateY(-1px); }
    .btn{ appearance:none; border:0; cursor:pointer; background: linear-gradient(135deg, var(--brand), var(--brand-2));
      color:white; font-weight:700; padding: 12px 18px; border-radius:14px; transition: transform var(--speed), filter var(--speed);
      box-shadow:0 10px 25px rgba(124,140,255,.35); display:flex; align-items:center; gap:8px; white-space:nowrap; }
    .btn:hover{ transform: translateY(-1px); filter: saturate(1.1); }
    .btn:active{ transform: translateY(0); }

    .main{ display:grid; grid-template-columns: minmax(280px, 1fr) minmax(380px, 520px); gap: 16px; align-items: start; }
    @media (max-width: 980px){ .main{ grid-template-columns: 1fr; } }
    #browsePanel{ grid-column: 1 / -1; }

    /* OPTIONAL: if you had .leftPanel before, retarget it to the new left results panel */
    #panelResultsStandalone{
      display:flex;
      flex-direction: column;
      gap:12px;
    }

    /* (Optional clarity) on mobile the span rule isn't needed, but harmless */
    @media (max-width:980px){
      #browsePanel{ grid-column: auto; }
    }


/* Give the inner results block a panel look without nesting .panel in .panel */
.subpanel{
  background: var(--card);
  border:1px solid #212938;
  border-radius: var(--radius);
  padding:16px;
}
    .grain{ position: fixed; inset: -10%; pointer-events: none; opacity: var(--grain-opacity);
      background-image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyMDAnIGhlaWdodD0nMjAwJz4KICA8ZmlsdGVyIGlkPSdmJz4KICAgIDxmZVR1cmJ1bGVuY2UgdHlwZT0nZnJhY3RhbE5vaXNlJyBiYXNlRnJlcXVlbmN5PScwLjknIG51bU9jdGF2ZXM9JzMnIHN0aXRjaFRpbGVzPSdzdGl0Y2gnLz4KICAgIDxmZUNvbG9yTWF0cml4IHR5cGU9J3NhdHVyYXRlJyB2YWx1ZXM9JzAnLz4KICA8L2ZpbHRlcj4KICA8cmVjdCB3aWR0aD0nMTAwJScgaGVpZ2h0PScxMDAlJyBmaWx0ZXI9J3VybCgjZiknLz4KPC9zdmc+);
      background-size: var(--grain-size); background-repeat: repeat; z-index: 0; mix-blend-mode: overlay; }

    /* Cards/Grid */
    .results-grid{ display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
    .card{ background: linear-gradient(180deg, #1b2230, #141a24); border:1px solid #263145; border-radius: 14px; padding: 12px;
      display:flex; gap:10px; align-items:center; cursor:pointer; transform: translateZ(0);
      transition: transform var(--speed), box-shadow var(--speed), border-color var(--speed); will-change: transform; }
    .card:hover{ transform: translateY(-3px); border-color: rgba(124,140,255,.45); box-shadow: 0 10px 30px rgba(124,140,255,.25); }
    .thumb{ width:56px;height:56px;border-radius:10px;flex-shrink:0; background:#0f131b;display:grid;place-items:center; font-size:22px;opacity:.9;overflow:hidden; }
    .thumb img{width:100%;height:100%;object-fit:cover;display:block}
    .meta{min-width:0}
    .meta .name{font-weight:700; font-size:14px; line-height:1.2; margin-bottom:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
    .meta .id{font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; color:var(--muted); font-size:12px}

    /* Tabs */
    .tabs{ display:flex; gap:8px; margin: -6px 0 12px; flex-wrap:wrap; }
    .tab{
      background: var(--chip); border:1px solid var(--chip-border); color:var(--text);
      padding:8px 12px; border-radius:999px; font-weight:700; font-size:12px; cursor:pointer;
      transition: transform var(--speed), filter var(--speed), border-color var(--speed);
    }
    .tab[aria-selected="true"]{
      border-color: rgba(124,140,255,.55);
      box-shadow: 0 6px 18px rgba(124,140,255,.22);
    }
    .tab:hover{ transform: translateY(-1px); filter: brightness(1.05); }

    .tab-panel[hidden]{ display:none; }
    .tab-panel .section-title{ font-weight:750; margin:6px 0 8px; }

    /* Album / tracks (right panel) */
    .album{ display:grid; gap:14px; }
    .album-header{ display:flex; gap:12px; align-items:flex-start; }
    .cover{ width:96px; height:96px; border-radius:12px; background:#0f131b; overflow:hidden; flex-shrink:0; border:1px solid #263145; }
    .cover img{width:100%;height:100%;object-fit:cover;display:block}
    .album-info{display:flex;flex-direction:column;gap:8px;min-width:0}
    .album-title{font-size:18px;font-weight:800;letter-spacing:.2px}
    .track-count{color:var(--muted);font-size:13px}

    .options{ display:grid; gap:10px; background: #121826; border:1px dashed rgba(124,140,255,.35); border-radius:14px; padding:12px; }
    .check{ display:flex; align-items:center; gap:10px; font-weight:600; }
    input[type="checkbox"]{ width:18px;height:18px; accent-color: var(--brand); }

    .track-list{ border:1px solid #263145; border-radius: 14px; overflow:hidden; }
    .track-toolbar{ display:flex; gap:8px; align-items:center; padding:10px 12px; border-bottom:1px solid #263145; background:#121826; }
    .pill{ background:var(--chip); border:1px solid var(--chip-border); border-radius:999px; color: var(--muted); padding:6px 10px; font-size:12px; cursor:pointer; user-select:none; transition: transform var(--speed), filter var(--speed); }
    .pill:hover{ transform: translateY(-1px); filter: brightness(1.05); }
    .pill.muted{ color:var(--muted); }
    .tracks{ max-height: 360px; overflow:auto; }
    .track{ display:grid; grid-template-columns: 24px 32px 1fr auto; align-items:center; gap:8px; padding:10px 12px; border-bottom:1px solid #1e2635; }
    .track:last-child{border-bottom:0}
    .tidx{color:var(--muted); text-align:right; padding-right:6px}
    .playbtn{ width:28px;height:28px;border-radius:8px;display:grid;place-items:center;cursor:pointer; }
    .tname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap; cursor:pointer;}
    .tchk{justify-self:end}

    .output{ display:grid; grid-template-columns: 1fr auto; gap: 10px; align-items:center; }
    .output .row{display:grid; grid-template-columns: 1fr auto; gap: 10px; align-items:center}
    .output .browse{ background:#1b2333; border:1px solid #2a3550; }

    .download-bar{ display:flex; gap:10px; align-items:center; justify-content:flex-start; }
    .download{ background: linear-gradient(135deg, var(--accent), #6ee7c2); color:#0f151e; font-weight:900; letter-spacing:.4px; padding: 12px 18px; border-radius: 14px; border:0; cursor:pointer; box-shadow: 0 12px 28px rgba(75,225,160,.35); transition: transform var(--speed), filter var(--speed), box-shadow var(--speed); }
    .download:hover{ transform: translateY(-2px); filter: saturate(1.05); }
    .download:disabled{ opacity:.6; cursor:not-allowed; filter: grayscale(.2) }

    .cancel{ background:#2b3244; color:#ffd5dc; border:1px solid #413347; padding: 10px 14px; border-radius: 12px; cursor:pointer; }

    .progress{ background:#0f151e; border:1px solid #263145; border-radius:14px; padding:14px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    .bar{ height:18px; background:#1a2231; border-radius:999px; overflow:hidden; border:1px solid #2a3550; margin:10px 0 8px; }
    .fill{ height:100%; width:0%; background: linear-gradient(90deg, var(--accent), var(--brand)); transition: width 300ms ease; }
    .log{max-height:180px; overflow:auto; font-size:12px; color:#b7c2d6; line-height:1.45}

    .reveal{opacity:0; transform: translateY(10px); transition: opacity .5s ease, transform .5s ease}
    .reveal.show{opacity:1; transform:none}

    .theme-fade{position:fixed; inset:0; background: var(--bg1); opacity:0; pointer-events:none; transition: opacity 240ms ease;}
    .theme-fade.on{opacity:1;}

    /* Downloads panel */
    .dl-toolbar{ display:flex; gap:8px; align-items:center; margin:6px 0 10px; }
    .dl-list{ display:grid; gap:10px; }
    .dl-item{ display:grid; grid-template-columns: 1fr auto; gap:8px; align-items:center; padding:10px 12px; border:1px solid #263145; border-radius:12px; background:#121826; }
    .dl-meta{ display:flex; gap:10px; align-items:center; min-width:0; }
    .dl-title{ font-weight:750; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .dl-sub{ color:var(--muted); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .chip{ padding:4px 8px; border-radius:999px; border:1px solid var(--chip-border); background:var(--chip); font-size:11px; font-weight:700; }
    .chip.ok{ border-color:#32d29666; }
    .chip.err{ border-color:#ff5c7c66; }
    .chip.run{ border-color:#a8d1ff66; }
  </style>

  <!-- Early theme set to avoid FOUC -->
  <script>
    (function(){
      try{
        const key='theme-preference';
        const saved=localStorage.getItem(key);
        const prefersDark=window.matchMedia('(prefers-color-scheme: dark)').matches;
        const theme = saved ? saved : (prefersDark ? 'dark' : 'light');
        document.documentElement.setAttribute('data-theme', theme);
      }catch(e){}
    })();
  </script>
</head>
<body>
  <div class="theme-fade" id="themeFade" aria-hidden="true"></div>
  <!-- default OFF to avoid the grainy look on load; JS toggles it -->
  <div class="grain" aria-hidden="true" style="display:none"></div>

  <div class="app">
    <div class="header reveal">
      <div class="logo"><span>🎵</span></div>
      <div class="title-wrap">
        <h1>Games SoundTracks Downloader 🎶</h1>
        <div class="subtitle">Search, preview, and download video-game soundtracks.</div>
      </div>

      <button id="settingsBtn" class="icon-btn" aria-label="Settings" title="Settings">⚙️</button>
      <button id="theme-toggle" class="theme-toggle" title="Toggle light & dark" aria-label="auto" aria-live="polite">
        <svg class="sun-and-moon" aria-hidden="true" width="24" height="24" viewBox="0 0 24 24">
          <mask class="moon" id="moon-mask">
            <rect x="0" y="0" width="100%" height="100%" fill="white" />
            <circle cx="24" cy="10" r="6" fill="black" />
          </mask>
          <circle class="sun" cx="12" cy="12" r="6" mask="url(#moon-mask)" fill="currentColor" />
          <g class="sun-beams" stroke="currentColor">
            <line x1="12" y1="1" x2="12" y2="3" />
            <line x1="12" y1="21" x2="12" y2="23" />
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
            <line x1="1" y1="12" x2="3" y2="12" />
            <line x1="21" y1="12" x2="23" y2="12" />
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
          </g>
        </svg>
      </button>
    </div>

    <div id="settingsSheet" class="sheet" hidden>
      <div class="sheet-inner">
        <div class="sheet-head">
          <strong>Appearance</strong>
          <button id="closeSettings" class="icon-btn">✕</button>
        </div>
        <div class="sheet-body">
          <!-- default OFF -->
          <label class="row"><input type="checkbox" id="grainToggle"> Grain overlay</label>
          <label class="row">Motion speed
            <input type="range" id="motionScale" min="0.7" max="1.3" step="0.05" value="1">
          </label>
          <label class="row"><input type="checkbox" id="themeToggle"> Light mode</label>
        </div>
      </div>
    </div>

    <div class="status reveal" id="status" role="status" aria-live="polite"></div>

    <div class="search-row reveal">
      <input id="searchInput" class="input" placeholder="Search soundtracks (e.g., “mario”, “zelda”, “final fantasy”)"/>
      <button id="searchButton" class="btn"><span>🔍</span> Search</button>
    </div>

    <div class="main">
  <!-- LEFT (top): Search Results -->
  <section id="panelResultsStandalone" class="panel reveal" hidden>
    <div class="section-title">Search Results</div>
    <div id="loading" style="display:none; padding:10px 0; color:var(--muted)">Searching…</div>
    <div id="resultsGrid" class="results-grid"></div>
  </section>

  <!-- RIGHT (top): Album details + download controls (unchanged content) -->
  <section class="panel reveal" id="rightPanel">
    <div class="album" id="albumDetails" style="display:none;">
      <div class="album-header">
        <div class="cover"><img id="albumCover" alt="" style="display:none"/></div>
        <div class="album-info">
          <div class="album-title" id="albumTitle">Album Title</div>
          <div class="track-count" id="trackCount">0 tracks</div>
          <div class="options">
            <label class="check"><input type="checkbox" id="downloadFullAlbum" checked/> Download entire album</label>
          </div>
        </div>
      </div>

      <div class="track-list" id="trackList" style="display:none;">
        <div class="track-toolbar">
          <input id="trackFilter" class="input" placeholder="Filter tracks (boss, remix...)" style="max-width:260px">
          <button class="pill" id="selectAllTracks">Select all</button>
          <button class="pill" id="deselectAllTracks">Deselect all</button>
          <div class="pill muted" id="selectedInfo" style="margin-left:auto">0 selected</div>
        </div>
        <div class="tracks" id="trackItems"></div>
      </div>
    </div>

    <div class="album" style="margin-top:10px">
      <div class="output">
        <div class="row">
          <input id="outputPath" class="input" value="" placeholder="Auto → Downloads\GameSoundtracks"/>
          <button id="browseBtn" class="btn browse">📁 Browse</button>
        </div>
      </div>

      <div class="download-bar" style="margin-top:12px">
        <button id="downloadButton" class="download" disabled>⬇ Download Soundtrack</button>
        <button id="cancelButton" class="cancel" style="display:none">Cancel</button>
      </div>

      <div class="progress" id="progressSection" style="display:none; margin-top:12px">
        <div id="progressText">Initializing…</div>
        <div class="bar"><div class="fill" id="progressFill"></div></div>
        <div class="log" id="progressLog"></div>
      </div>
    </div>
    </section>

    <!-- BOTTOM (full width): Popular / Latest / Downloads -->
    <section class="panel reveal" id="browsePanel" style="grid-column:1 / -1">
      <div class="tabs" id="tabsBar" role="tablist" aria-label="Browse">
        <button class="tab" data-tab="popular" role="tab" aria-selected="true">Popular</button>
        <button class="tab" data-tab="latest" role="tab" aria-selected="false">Latest</button>
        <button class="tab" data-tab="downloads" role="tab" aria-selected="false">Downloads</button>
        <button class="tab" data-tab="favorites" role="tab" aria-selected="false">Favorites</button>
      </div>

      <!-- Popular Panel -->
      <div id="panelPopular" class="tab-panel" role="tabpanel" aria-labelledby="popular">
        <div class="section-title">Popular Series</div>
        <div id="popularGrid" class="results-grid"></div>
      </div>

      <!-- Latest Panel -->
      <div id="panelLatest" class="tab-panel" role="tabpanel" hidden aria-labelledby="latest">
        <div class="section-title">Latest Soundtracks</div>
        <div id="latestGrid" class="results-grid"></div>
      </div>

      <!-- Downloads Panel -->
      <div id="panelDownloads" class="tab-panel" role="tabpanel" hidden aria-labelledby="downloads">
        <div class="dl-toolbar">
          <button id="refreshDownloads" class="pill">Refresh</button>
          <button id="clearDownloads" class="pill">Clear history</button>
        </div>
        <div id="dlList" class="dl-list"></div>
      </div>
      <div id="panelFavorites" class="tab-panel" role="tabpanel" hidden aria-labelledby="favorites">
        <div class="section-title">Favorites</div>
        <div id="favList" class="dl-list"></div>
      </div>

    </section>
  </div>


    <!-- Footer Player -->
    <footer class="player panel" id="footerPlayer" hidden>
      <div class="track-meta">
        <div class="cover"><img id="playerCover" alt=""/></div>
        <div class="meta">
          <div class="title" id="playerTitle">—</div>
          <div class="subtitle" id="playerSubtitle">—</div>
        </div>
      </div>

      <div class="controls">
        <button id="btnShuffle" class="ctrl-btn" title="Shuffle" aria-pressed="false">
          <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 7h3c2 0 3 .7 4 2l2 2c1 .9 2 2 4 2h3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M4 17h3c2 0 3-.7 4-2l2-2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M18 5l3 3-3 3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M18 13l3 3-3 3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <button id="btnPrev" class="ctrl-btn" title="Previous">
          <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
            <polygon points="11,12 19,6 19,18" fill="currentColor"/>
            <rect x="5" y="6" width="3" height="12" fill="currentColor"/>
          </svg>
        </button>
        <button id="btnPlay" class="ctrl-btn" title="Play/Pause">
          <svg id="icPlay" width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
            <polygon points="8,5 20,12 8,19" fill="currentColor"/>
          </svg>
          <svg id="icPause" width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" style="display:none">
            <rect x="6" y="5" width="4" height="14" fill="currentColor"/>
            <rect x="14" y="5" width="4" height="14" fill="currentColor"/>
          </svg>
        </button>
        <button id="btnNext" class="ctrl-btn" title="Next">
          <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
            <polygon points="5,6 13,12 5,18" fill="currentColor"/>
            <rect x="16" y="6" width="3" height="12" fill="currentColor"/>
          </svg>
        </button>
        <button id="btnRepeat" class="ctrl-btn" title="Repeat" aria-pressed="false">
          <svg id="icRepeatAll" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M7 7h7a4 4 0 0 1 4 4v1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M17 7l-3-3 3-3" transform="translate(0,6)" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M17 17H10a4 4 0 0 1-4-4v-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M7 17l3 3-3 3" transform="translate(0,-6)" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <svg id="icRepeatOne" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" style="display:none">
            <path d="M7 7h7a4 4 0 0 1 4 4v1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M17 7l-3-3 3-3" transform="translate(0,6)" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M17 17H10a4 4 0 0 1-4-4v-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M7 17l3 3-3 3" transform="translate(0,-6)" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <text x="12.5" y="13.5" font-size="7" fill="currentColor" font-family="ui-sans-serif, system-ui" text-anchor="middle">1</text>
          </svg>
        </button>
      </div>

      <div class="right">
        <span id="curTime" class="time">0:00</span>
        <input id="seek" type="range" min="0" max="1" step="1" value="0"/>
        <span id="durTime" class="time">0:00</span>
        <input id="vol" type="range" min="0" max="1" step="0.01"/>
      </div>

      <audio id="audio" preload="none" crossorigin="anonymous"></audio>
    </footer>
  </div>

  <script>
    const API_BASE = '';
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => Array.from(document.querySelectorAll(sel));

    const searchInput = $('#searchInput');
    const searchButton = $('#searchButton');
    const loading = $('#loading');
    const status = $('#status');

    // Left panels
    const tabsBar = $('#tabsBar');
    const panelPopular   = $('#panelPopular');
    const panelLatest    = $('#panelLatest');
    const panelResultsStandalone = $('#panelResultsStandalone');

    const panelDownloads = $('#panelDownloads');

    const popularGrid = $('#popularGrid');
    const latestGrid  = $('#latestGrid');
    const resultsGrid = $('#resultsGrid');

    // Right panel (album / download)
    const albumDetails = $('#albumDetails');
    const albumCover = $('#albumCover');
    const albumTitle = $('#albumTitle');
    const trackCount = $('#trackCount');
    const downloadFullAlbum = $('#downloadFullAlbum');
    const trackList = $('#trackList');
    const trackItems = $('#trackItems');
    const selectAllTracks = $('#selectAllTracks');
    const deselectAllTracks = $('#deselectAllTracks');
    const selectedInfo = $('#selectedInfo');
    const outputPath = $('#outputPath');
    const downloadButton = $('#downloadButton');
    const cancelButton = $('#cancelButton');
    const progressSection = $('#progressSection');
    const progressText = $('#progressText');
    const progressFill = $('#progressFill');
    const progressLog = $('#progressLog');
    const browseBtn = $('#browseBtn');

    // Downloads panel elements
    const dlList = $('#dlList');
    const refreshDownloads = $('#refreshDownloads');
    const clearDownloads = $('#clearDownloads');

    // Player elements
    const footerPlayer = $('#footerPlayer');
    const playerCover = $('#playerCover');
    const playerTitle = $('#playerTitle');
    const playerSubtitle = $('#playerSubtitle');
    const btnShuffle = $('#btnShuffle');
    const btnPrev = $('#btnPrev');
    const btnPlay = $('#btnPlay');
    const btnNext = $('#btnNext');
    const btnRepeat = $('#btnRepeat');
    const vol = $('#vol');
    const seek = $('#seek');
    const curTime = $('#curTime');
    const durTime = $('#durTime');
    const audio = $('#audio');
    const icPlay = $('#icPlay'), icPause = $('#icPause');
    const icRepeatAll = $('#icRepeatAll'), icRepeatOne = $('#icRepeatOne');

    let queue = [];
    let currentIndex = -1;
    let shuffleOn = JSON.parse(localStorage.getItem('gsd_shuffle') || 'false');
    let repeatMode = localStorage.getItem('gsd_repeat') || 'off'; // 'off' | 'all' | 'one'
    let isScrubbing = false;

    let currentAlbum = null;
    let currentProgressId = null;
    let progressInterval = null;

    // ===== Utilities =====
    function showStatus(message, isError=false){
      status.textContent = message;
      status.className = isError ? 'status error reveal show' : 'status reveal show';
      status.style.display = 'block';
      if(!isError){ setTimeout(()=>{ status.style.display='none' }, 4500); }
    }
    function showLoading(show){ if(loading) loading.style.display = show ? 'block':'none' }

    const io = new IntersectionObserver((entries)=>{
      entries.forEach(e=>{ if(e.isIntersecting){ e.target.classList.add('show') } })
    }, {threshold:.08});
    $$('.reveal').forEach(el=> io.observe(el));

    // ===== Tabs logic =====
    function switchTab(name){
      const map = {popular:panelPopular, latest:panelLatest, downloads:panelDownloads, favorites:panelFavorites};
      Object.entries(map).forEach(([k,el])=>{ if(el) el.hidden = (k !== name); });
      $$('#tabsBar .tab').forEach(btn=> btn.setAttribute('aria-selected', String(btn.dataset.tab === name)));
      if(name==='downloads') renderDownloads();
      if(name==='favorites') renderFavorites();
    }
    tabsBar.addEventListener('click', (e)=>{
      const btn = e.target.closest('.tab');
      if(!btn) return;
      switchTab(btn.dataset.tab);
      if(btn.dataset.tab === 'downloads'){ renderDownloads(); }
    });

    // ===== Home (Popular/Latest) =====
    function hydrateMissingIcons(list, cardMap){
      const need = (list||[]).filter(a => a && !a.icon).map(a => a.id);
      if(!need.length) return;
      const concurrency = 4;
      let index = 0;
      async function worker(){
        while(index < need.length){
          const id = need[index++];
          try{
            const res = await fetch(`${API_BASE}/album/${id}`);
            if(!res.ok) continue;
            const info = await res.json();
            if(info && info.icon && cardMap[id]){
              const thumb = cardMap[id].querySelector('.thumb');
              if (thumb) thumb.innerHTML = `<img src="${info.icon}" alt="" loading="lazy">`;
            }
          }catch(e){}
        }
      }
      return Promise.all(Array.from({length: Math.min(concurrency, need.length)}, worker));
    }

    function renderAlbumGrid(container, list){
      if(!container) return;
      container.innerHTML = (list||[]).map(a=>{
        const safeName = (a.name||'').replace(/'/g,"\\'");
        const imgHtml = a.icon ? `<img src="${a.icon}" alt="" loading="lazy">` : '';
        return `
          <div class="card" data-id="${a.id}" data-name="${safeName}">
            <div class="thumb">${imgHtml}</div>
            <div class="meta">
              <div class="name">${a.name||''}</div>
              <div class="id">ID: ${a.id||''}</div>
            </div>
          </div>`;
      }).join('');

      const cardMap = {};
      container.querySelectorAll('.card').forEach(card=>{
        cardMap[card.dataset.id] = card;
        card.addEventListener('click', ()=> selectAlbum(card.dataset.id, card.dataset.name));
      });
      hydrateMissingIcons(list||[], cardMap);
    }
    function renderSeriesGrid(container, list){
      if(!container) return;
      container.innerHTML = (list||[]).map(s=>{
        const safeName = (s.name||'').replace(/'/g,"\\'");
        // Use a "card" look, but it's a series, so clicking triggers a search
        return `
          <div class="card series" data-name="${safeName}">
            <div class="thumb">🎮</div>
            <div class="meta">
              <div class="name">${s.name||''}</div>
              <div class="id">Series: ${s.id||''}</div>
            </div>
          </div>`;
      }).join('');

      container.querySelectorAll('.series').forEach(card=>{
        card.addEventListener('click', ()=>{
          const q = card.dataset.name || '';
          if(q){ searchInput.value = q; performSearch(); }
        });
      });
    }

    async function loadHome(){
      try{
        const r = await fetch(`${API_BASE}/home`);
        if(!r.ok) return;
        const data = await r.json();
        renderSeriesGrid(popularGrid, data.popular||[]);
        renderAlbumGrid(latestGrid,  data.latest||[]);
      }catch(_){}
    }

    // ===== Search / Results =====
    async function performSearch(){
      const q = searchInput.value.trim();
      if(!q){ showStatus('Please enter a search term', true); return; }
      
      showLoading(true);
      
      // Show the standalone results panel
      if (panelResultsStandalone) panelResultsStandalone.hidden = false;
      if (resultsGrid) resultsGrid.innerHTML = '';
      albumDetails.style.display='none';

      try{
      const res = await fetch(`${API_BASE}/search`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({query:q})
      });
      if(!res.ok) throw new Error(`Search failed: ${res.statusText}`);
      const data = await res.json();
      showLoading(false);
      renderResults(data.results||[]);
      if((data.results||[]).length){ showStatus(`Found ${data.results.length} albums`) }
      else { showStatus('No albums found', true) }

      // scroll results into view for UX
      panelResultsStandalone?.scrollIntoView({behavior:'smooth', block:'start'});
      }catch(err){
        showLoading(false);
        showStatus(err.message, true);
      }
    }

    function renderResults(results){
      if(!results.length){ resultsGrid.innerHTML=''; return; }
      resultsGrid.innerHTML = results.map(a=>{
        const safeName = a.name.replace(/'/g,"\\'");
        const imgHtml = a.icon ? `<img src="${a.icon}" alt="" loading="lazy">` : '';
        return `
          <div class="card" data-id="${a.id}" data-name="${safeName}">
            <div class="thumb">${imgHtml}</div>
            <div class="meta">
              <div class="name">${a.name}</div>
              <div class="id">ID: ${a.id}</div>
            </div>
          </div>`;
      }).join('');
      const cardMap = {};
      $$('#resultsGrid .card').forEach(card=>{
        cardMap[card.dataset.id] = card;
        card.addEventListener('click', ()=> selectAlbum(card.dataset.id, card.dataset.name));
      });
      // Fill missing icons
      (async ()=>{
        const need = results.filter(a=>!a.icon).map(a=>a.id);
        if(!need.length) return;
        for(const id of need){
          try{
            const r = await fetch(`${API_BASE}/album/${id}`);
            if(!r.ok) continue;
            const info = await r.json();
            if(info && info.icon && cardMap[id]){
              const thumb = cardMap[id].querySelector('.thumb');
              thumb.innerHTML = `<img src="${info.icon}" alt="" loading="lazy">`;
            }
          }catch(e){}
        }
      })();
    }

    // ===== Album / Tracks =====
    async function selectAlbum(albumId){
      showLoading(true);
      albumDetails.style.display='none';
      try{
        const res = await fetch(`${API_BASE}/album/${albumId}`);
        if(!res.ok) throw new Error(`Failed to load album: ${res.statusText}`);
        const album = await res.json();
        showLoading(false);
        displayAlbum(album);
        showStatus(`Loaded “${album.title}” with ${album.total_tracks} tracks`);
      }catch(err){
        showLoading(false);
        showStatus(err.message, true);
      }
    }

    function displayAlbum(album){
      currentAlbum = album;
      albumTitle.textContent = album.title;
      trackCount.textContent = `${album.total_tracks} tracks`;
      if(album.icon){
        albumCover.src = album.icon;
        albumCover.style.display = 'block';
        albumCover.onerror = () => albumCover.style.display = 'none';
      }else{
        albumCover.style.display = 'none';
      }
      populateTracks(album.tracks || []);
      downloadFullAlbum.checked = true;
      trackList.style.display = 'none';
      updateDownloadButton();
      albumDetails.style.display='block';
    }

    function populateTracks(tracks){
      trackItems.innerHTML = tracks.map((t,i)=>`
        <div class="track" data-idx="${i}">
          <div class="tidx">${t.number}</div>
          <div class="playbtn" title="Play" data-url="${t.url}" aria-label="Play">▶</div>
          <div class="favbtn" title="Favorite" data-url="${t.url}" aria-label="Favorite">☆</div>
          <div class="tname" data-url="${t.url}" title="Double-click to play">${t.name}</div>
          <input type="checkbox" class="tchk" value="${t.name}" checked/>
        </div>
      `).join('');

      $$('#trackItems .tchk').forEach(cb => cb.addEventListener('change', updateSelectedCount));
      updateSelectedCount();
      if (trackFilter) filterRows(trackFilter.value);

      $$('#trackItems .playbtn').forEach(btn=>{
        btn.addEventListener('click', ()=>{
          buildQueueFromCurrentAlbum();
          const row = btn.closest('.track');
          const idx = parseInt(row.dataset.idx,10);
          playTrackAt(idx);
        });
      });
      // Favorites
      $$('#trackItems .favbtn').forEach(btn=>{
        const row = btn.closest('.track');
        const idx = parseInt(row.dataset.idx,10);
        const track = tracks[idx];
        if (isFav(track.url)) btn.classList.add('on'), btn.textContent = '★';
        btn.addEventListener('click', ()=>{
          toggleFav(track, currentAlbum);
          const on = btn.classList.toggle('on');
          btn.textContent = on ? '★' : '☆';
          if(!panelFavorites.hidden) renderFavorites();
          showStatus(on ? 'Added to favorites' : 'Removed from favorites');
        });
      });
      $$('#trackItems .tname').forEach(el=>{
        el.addEventListener('dblclick', ()=>{
          buildQueueFromCurrentAlbum();
          const row = el.closest('.track');
          const idx = parseInt(row.dataset.idx,10);
          playTrackAt(idx);
        });
      });
    }

    downloadFullAlbum.addEventListener('change', ()=>{
      trackList.style.display = downloadFullAlbum.checked ? 'none' : 'block';
      updateDownloadButton();
    });
    selectAllTracks.addEventListener('click', ()=>{
      $$('#trackItems .track').forEach(row=>{
        if (row.style.display !== 'none') row.querySelector('.tchk').checked = true;
      });
      updateSelectedCount();
    });
    deselectAllTracks.addEventListener('click', ()=>{
      $$('#trackItems .track').forEach(row=>{
        if (row.style.display !== 'none') row.querySelector('.tchk').checked = false;
      });
      updateSelectedCount();
    });

    function updateSelectedCount(){
      const all = $$('#trackItems .tchk').length;
      const sel = $$('#trackItems .tchk:checked').length;
      selectedInfo.textContent = `${sel} of ${all} selected`;
      updateDownloadButton();
    }
    function getSelectedTracks(){
      if(downloadFullAlbum.checked) return null;
      return $$('#trackItems .tchk:checked').map(cb=> cb.value);
    }

    const trackFilter = $('#trackFilter');
    if (trackFilter) trackFilter.addEventListener('input', ()=> filterRows(trackFilter.value));
    function filterRows(q){
      const needle = (q || '').trim().toLowerCase();
      $$('#trackItems .track').forEach(row=>{
        const name = row.querySelector('.tname').textContent.toLowerCase();
        row.style.display = !needle || name.includes(needle) ? 'grid' : 'none';
      });
    }

    function updateDownloadButton(){
      const hasAlbum = !!currentAlbum;
      const full = downloadFullAlbum.checked;
      const hasSel = $$('#trackItems .tchk:checked').length > 0;
      downloadButton.disabled = !(hasAlbum && (full || hasSel));
    }

    // ===== Downloads History (left panel) =====
    function getDlHistory(){
      try{ return JSON.parse(localStorage.getItem('gsd_downloads')||'[]'); }catch(_){ return []; }
    }
    function setDlHistory(arr){
      localStorage.setItem('gsd_downloads', JSON.stringify(arr||[]));
    }
    function upsertDlRecord(rec){
      const list = getDlHistory();
      const i = list.findIndex(x=> x.id === rec.id);
      if(i>=0) list[i] = {...list[i], ...rec}; else list.unshift(rec);
      setDlHistory(list);
    }
    function renderDownloads(){
      const list = getDlHistory();
      dlList.innerHTML = list.length ? list.map(r=>{
        const date = new Date(r.when||Date.now());
        const nice = date.toLocaleString();
        const chipClass = r.status==='completed' ? 'chip ok'
                         : r.status==='downloading' ? 'chip run'
                         : (r.status==='cancelled'||r.status==='error') ? 'chip err' : 'chip';
        const sub = [
          r.albumId ? `ID: ${r.albumId}` : '',
          r.count ? `${r.count} tracks` : '',
          r.path ? r.path : '',
          nice
        ].filter(Boolean).join(' • ');
        return `
          <div class="dl-item" data-id="${r.id}" data-album="${r.albumId||''}">
            <div class="dl-meta">
              <div>
                <div class="dl-title">${r.title||'—'}</div>
                <div class="dl-sub">${sub}</div>
              </div>
            </div>
            <span class="${chipClass}">${(r.status||'—').toUpperCase()}</span>
          </div>`;
      }).join('') : `<div class="dl-sub">No downloads yet.</div>`;

      // Click to reopen album
      dlList.querySelectorAll('.dl-item').forEach(el=>{
        el.addEventListener('click', ()=>{
          const aid = el.dataset.album;
          if(aid) selectAlbum(aid);
        });
      });
    }
    refreshDownloads.addEventListener('click', renderDownloads);
    clearDownloads.addEventListener('click', ()=>{
      setDlHistory([]);
      renderDownloads();
    });
    // ===== Favorites (localStorage) =====
    function getFavs(){ try{ return JSON.parse(localStorage.getItem('gsd_favs')||'[]'); }catch(_){ return []; } }
    function setFavs(arr){ localStorage.setItem('gsd_favs', JSON.stringify(arr||[])); }
    function isFav(url){ return getFavs().some(f => f.url === url); }
    function toggleFav(track, album){
      const list = getFavs();
      const i = list.findIndex(f => f.url === track.url);
      if(i>=0){ list.splice(i,1); setFavs(list); return; }
      list.unshift({
        url: track.url,
        name: track.name,
        number: track.number,
        albumId: album.id,
        albumTitle: album.title,
        icon: album.icon || null
      });
      setFavs(list);
    }
    function renderFavorites(){
      const list = getFavs();
      const el = $('#favList');
      if(!el) return;
      el.innerHTML = list.length ? list.map(f=>`
        <div class="dl-item">
          <div class="dl-meta">
            <div class="thumb" style="width:40px;height:40px;border-radius:8px;overflow:hidden;">
              ${f.icon ? `<img src="${f.icon}" alt="" loading="lazy" style="width:100%;height:100%;object-fit:cover">` : '🎵'}
            </div>
            <div>
              <div class="dl-title">${f.name}</div>
              <div class="dl-sub">${f.albumTitle||''}</div>
            </div>
          </div>
          <div style="display:flex;gap:8px;">
            <button class="pill play-fav" data-url="${f.url}">Play</button>
            <button class="pill remove-fav" data-url="${f.url}">Remove</button>
          </div>
        </div>
      `).join('') : `<div class="dl-sub">No favorites yet.</div>`;

      el.querySelectorAll('.play-fav').forEach(btn=>{
        btn.addEventListener('click', ()=>{
          queue = [{ name: btn.closest('.dl-item').querySelector('.dl-title').textContent, url: btn.dataset.url, number: 1 }];
          currentIndex = 0;
          audio.src = `/stream?p=${encodeURIComponent(btn.dataset.url)}`;
          audio.play().catch(()=>{});
          updatePlayerUI();
        });
      });
      el.querySelectorAll('.remove-fav').forEach(btn=>{
        btn.addEventListener('click', ()=>{
          const next = getFavs().filter(x=> x.url !== btn.dataset.url);
          setFavs(next);
          renderFavorites();
          // reflect on any visible star
          document.querySelectorAll(`.favbtn[data-url="${btn.dataset.url}"]`).forEach(b=>{ b.classList.remove('on'); b.textContent='☆'; });
        });
      });
    }


    // ===== Start/Track download =====
    async function startDownload(){
      if(!currentAlbum){ showStatus('No album selected', true); return; }
      const selectedTracks = getSelectedTracks();
      const outputDirRaw = (outputPath.value || '').trim();
      if(selectedTracks !== null && !selectedTracks.length){
        showStatus('Please select at least one track', true); return;
      }
      try{
        downloadButton.disabled = true;
        downloadButton.textContent = '⏳ Starting…';
        cancelButton.style.display = 'inline-block';
        const res = await fetch(`${API_BASE}/download`, {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ album_id: currentAlbum.id, output_path: outputDirRaw, selected_tracks: selectedTracks })
        });
        if(!res.ok) throw new Error(`Download failed: ${res.statusText}`);
        const data = await res.json();
        if(data.error) throw new Error(data.error);
        currentProgressId = data.progress_id;

        // seed downloads record
        upsertDlRecord({
          id: currentProgressId,
          albumId: currentAlbum.id,
          title: currentAlbum.title,
          when: Date.now(),
          status: 'downloading',
          path: outputDirRaw || 'Auto',
          count: selectedTracks ? selectedTracks.length : (currentAlbum.total_tracks||0)
        });
        if(!panelDownloads.hidden) renderDownloads();

        trackProgress();
        showStatus('Download started!');
      }catch(err){
        resetDownloadUI();
        showStatus(err.message, true);
      }
    }
    function resetDownloadUI(){
      downloadButton.disabled = false;
      downloadButton.textContent = '⬇ Download Soundtrack';
      cancelButton.style.display = 'none';
      updateDownloadButton();
    }
    async function trackProgress(){
      progressSection.style.display = 'block';
      progressLog.innerHTML = '';
      if(progressInterval) clearInterval(progressInterval);
      progressInterval = setInterval(async ()=>{
        try{
          const res = await fetch(`${API_BASE}/progress/${currentProgressId}`);
          if(!res.ok) throw new Error('Failed to get progress');
          const p = await res.json();
          const pct = p.total_tracks>0 ? Math.round((p.current_track/p.total_tracks)*100) : 0;
          progressFill.style.width = pct + '%';
          progressText.textContent = `${p.current_track||0}/${p.total_tracks||0} — ${p.message || 'Working…'}`;
          if(p.current_file && progressLog.dataset.lastFile !== p.current_file){
            const div = document.createElement('div');
            div.textContent = '✅ ' + p.current_file;
            progressLog.appendChild(div);
            progressLog.dataset.lastFile = p.current_file;
            progressLog.scrollTop = progressLog.scrollHeight;
          }
          // Update downloads record
          if(['completed','error','cancelled'].includes(p.status)){
            upsertDlRecord({
              id: currentProgressId,
              status: p.status,
              when: Date.now()
            });
            if(!panelDownloads.hidden) renderDownloads();
            clearInterval(progressInterval);
            resetDownloadUI();
            currentProgressId = null;
          }
        }catch(err){
          clearInterval(progressInterval);
          resetDownloadUI();
          showStatus('Lost connection to download', true);
        }
      }, 1000);
    }
    async function cancelDownload(){
      if(!currentProgressId) return;
      try{ await fetch(`${API_BASE}/cancel/${currentProgressId}`, {method:'POST'}); showStatus('Download cancelled', true); }catch(e){}
      upsertDlRecord({ id: currentProgressId, status: 'cancelled', when: Date.now() });
      if(!panelDownloads.hidden) renderDownloads();
      if(progressInterval) clearInterval(progressInterval);
      resetDownloadUI();
      currentProgressId = null;
    }

    async function browseFolder(){
      if('showDirectoryPicker' in window){
        try{
          const handle = await window.showDirectoryPicker();
          outputPath.value = handle.name;
          showStatus(`📁 Selected folder: ${handle.name}`);
        }catch(e){ if(e.name!=='AbortError'){ showStatus('Folder selection failed', true); } }
      }else{
        showStatus('Your browser does not support native folder picking. Manually type the path.', true);
      }
    }

    window.addEventListener('beforeunload', () => {
      try { navigator.sendBeacon('/quit', new Blob([], {type: 'text/plain'})); } catch (_) {}
    });

    const quitBtn = document.querySelector('#quitApp');
    if (quitBtn) {
      quitBtn.addEventListener('click', async () => {
        try { await fetch('/quit', {method:'POST'}); } catch (_){}
      });
    }

    document.addEventListener('keydown', (e)=>{
      const tag = (document.activeElement && document.activeElement.tagName) || '';
      const typing = tag === 'INPUT' || tag === 'TEXTAREA';
      if(e.key === '/' && !typing){ e.preventDefault(); searchInput.focus(); }
      if((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'j'){ e.preventDefault(); startDownload(); }
      if(e.key === 'a' && !typing){ e.preventDefault(); selectAllTracks.click(); }
      if(e.key === 'd' && !typing){ e.preventDefault(); deselectAllTracks.click(); }
      if(e.key === 'Escape'){ cancelButton.click(); }
    });

    searchButton.addEventListener('click', performSearch);
    searchInput.addEventListener('keydown', e=> { if(e.key==='Enter') performSearch() });
    downloadFullAlbum.addEventListener('change', updateDownloadButton);
    downloadButton.addEventListener('click', startDownload);
    cancelButton.addEventListener('click', cancelDownload);
    browseBtn.addEventListener('click', browseFolder);

    // ===== Theme & settings =====
    const settingsSheet = $('#settingsSheet');
    $('#settingsBtn').addEventListener('click', ()=> settingsSheet.hidden = false);
    $('#closeSettings').addEventListener('click', ()=> settingsSheet.hidden = true);
    settingsSheet.addEventListener('click', e => { if(e.target === settingsSheet) settingsSheet.hidden = true; });

    // Grain default OFF, persist
    const grainEl = document.querySelector('.grain');
    const grainToggle = $('#grainToggle');
    const storedGrain = localStorage.getItem('grain');
    const grainOn = storedGrain ? storedGrain === '1' : false; // default off
    grainToggle.checked = grainOn;
    if (grainEl) grainEl.style.display = grainOn ? 'block' : 'none';
    grainToggle.addEventListener('change', ()=>{
      const on = grainToggle.checked;
      if (grainEl) grainEl.style.display = on ? 'block' : 'none';
      localStorage.setItem('grain', on ? '1' : '0');
    });

    const root = document.documentElement;
    const motionScale = $('#motionScale');
    const BASE_SPEED_MS = 280;
    motionScale.value = localStorage.getItem('motionScale') || '1';
    root.style.setProperty('--speed', (BASE_SPEED_MS * parseFloat(motionScale.value)) + 'ms');
    motionScale.addEventListener('input', ()=>{
      root.style.setProperty('--speed', (BASE_SPEED_MS * parseFloat(motionScale.value)) + 'ms');
      localStorage.setItem('motionScale', motionScale.value);
    });

    const storageKey = 'theme-preference';
    const prefersReduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const getColorPreference = () => {
      const saved = localStorage.getItem(storageKey);
      if (saved) return saved;
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    };
    const theme = { value: getColorPreference() };

    const reflectPreference = () => {
      document.documentElement.setAttribute('data-theme', theme.value);
      const btn = document.querySelector('#theme-toggle');
      if (btn) btn.setAttribute('aria-label', theme.value);
      const chk = document.querySelector('#themeToggle');
      if (chk) chk.checked = (theme.value === 'light');
    };

    const setPreference = () => {
      localStorage.setItem(storageKey, theme.value);
      reflectPreference();
    };

    function applyThemeWithFade(next){
      const veil = document.getElementById('themeFade');
      if(!veil || prefersReduceMotion){
        theme.value = next; setPreference(); return;
      }
      veil.classList.add('on');
      setTimeout(()=>{
        theme.value = next; setPreference();
        setTimeout(()=> veil.classList.remove('on'), 240);
      }, 80);
    }

    reflectPreference();
    const themeToggleBtn = document.querySelector('#theme-toggle');
    if (themeToggleBtn) {
      themeToggleBtn.addEventListener('click', () => {
        const next = (theme.value === 'light') ? 'dark' : 'light';
        applyThemeWithFade(next);
      });
    }
    const themeToggleChk = document.querySelector('#themeToggle');
    if (themeToggleChk){
      themeToggleChk.addEventListener('change', ()=>{
        applyThemeWithFade(themeToggleChk.checked ? 'light' : 'dark');
      });
    }
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    media.addEventListener('change', ({matches:isDark})=>{
      if (!localStorage.getItem(storageKey)){
        applyThemeWithFade(isDark ? 'dark' : 'light');
      }
    });

    // ===== Player logic =====
    function buildQueueFromCurrentAlbum(){
      queue = (currentAlbum && currentAlbum.tracks) ? [...currentAlbum.tracks] : [];
      currentIndex = -1;
    }
    // Volume
    vol.value = localStorage.getItem('gsd_vol') || '0.9';
    audio.volume = parseFloat(vol.value);
    vol.addEventListener('input', ()=>{
      audio.volume = parseFloat(vol.value);
      localStorage.setItem('gsd_vol', vol.value);
    });

    // Transport
    btnPlay.addEventListener('click', ()=>{
      if (audio.paused) audio.play(); else audio.pause();
      updatePlayerUI();
    });
    btnNext.addEventListener('click', ()=>{ const ni = nextIndex(); if (ni >= 0) playTrackAt(ni); });
    btnPrev.addEventListener('click', ()=>{ const pi = prevIndex(); if (pi >= 0) playTrackAt(pi); });

    // Shuffle
    btnShuffle.addEventListener('click', ()=>{
      shuffleOn = !shuffleOn;
      localStorage.setItem('gsd_shuffle', JSON.stringify(shuffleOn));
      updatePlayerUI();
    });

    // Repeat: off -> all -> one
    btnRepeat.addEventListener('click', ()=>{
      repeatMode = (repeatMode === 'off') ? 'all' : (repeatMode === 'all' ? 'one' : 'off');
      localStorage.setItem('gsd_repeat', repeatMode);
      audio.loop = (repeatMode === 'one'); // keep loop in sync
      updatePlayerUI();
    });

    audio.addEventListener('play', updatePlayerUI);
    audio.addEventListener('pause', updatePlayerUI);

    // Time + seek
    function fmtTime(sec){
      if (!isFinite(sec)) return '0:00';
      sec = Math.max(0, Math.floor(sec));
      const m = Math.floor(sec / 60), s = sec % 60;
      return `${m}:${s.toString().padStart(2,'0')}`;
    }
    function updateTimeUI(){
      curTime.textContent = fmtTime(audio.currentTime);
      durTime.textContent = fmtTime(audio.duration);
      if (!isScrubbing){
        const max = isFinite(audio.duration) ? Math.max(1, Math.floor(audio.duration)) : 1;
        if (String(seek.max) !== String(max)) seek.max = max;
        seek.value = isFinite(audio.currentTime) ? Math.floor(audio.currentTime) : 0;
      }
    }
    seek.addEventListener('input', ()=>{
      isScrubbing = true;
      curTime.textContent = fmtTime(parseFloat(seek.value||'0'));
    });
    seek.addEventListener('change', ()=>{
      const to = parseFloat(seek.value||'0');
      audio.currentTime = isFinite(to) ? to : 0;
      isScrubbing = false;
    });

    audio.addEventListener('timeupdate', updateTimeUI);
    audio.addEventListener('loadedmetadata', ()=>{
      audio.loop = (repeatMode === 'one');
      updateTimeUI();
    });
    audio.addEventListener('ended', ()=>{
      if (repeatMode === 'one') return; // loop handles it
      const ni = nextIndex();
      if (ni >= 0) playTrackAt(ni);
      else { currentIndex = -1; updatePlayerUI(); }
    });

    function updatePlayerUI(){
      footerPlayer.hidden = (currentIndex < 0 || !queue.length);
      if (currentAlbum && currentAlbum.icon) playerCover.src = currentAlbum.icon; else playerCover.removeAttribute('src');
      if (currentIndex >= 0 && queue[currentIndex]){
        playerTitle.textContent = queue[currentIndex].name || '—';
        playerSubtitle.textContent = currentAlbum ? currentAlbum.title : '—';
      } else {
        playerTitle.textContent = '—';
        playerSubtitle.textContent = '—';
      }
      const playing = !audio.paused && !audio.ended;
      icPlay.style.display = playing ? 'none' : '';
      icPause.style.display = playing ? '' : 'none';
      btnShuffle.classList.toggle('active', !!shuffleOn);
      btnShuffle.setAttribute('aria-pressed', String(!!shuffleOn));
      const repActive = repeatMode !== 'off';
      btnRepeat.classList.toggle('active', repActive);
      btnRepeat.setAttribute('aria-pressed', String(repActive));
      icRepeatAll.style.display = (repeatMode === 'one') ? 'none' : '';
      icRepeatOne.style.display = (repeatMode === 'one') ? '' : 'none';
    }

    async function playTrackAt(idx){
      if (idx < 0 || idx >= queue.length) return;
      currentIndex = idx;
      const track = queue[currentIndex];
      const src = `/stream?p=${encodeURIComponent(track.url)}`;
      const wasMuted = audio.muted;
      const volSaved = parseFloat(localStorage.getItem('gsd_vol') || '0.9');
      audio.src = src;
      audio.muted = wasMuted;
      audio.volume = volSaved;
      audio.loop = (repeatMode === 'one');
      updatePlayerUI();
      try { await audio.play(); } catch(_) {}
      updatePlayerUI();
    }

    function nextIndex(){
      if (!queue.length) return -1;
      if (shuffleOn){
        if (queue.length === 1) return currentIndex;
        let n; do { n = Math.floor(Math.random()*queue.length); } while(n === currentIndex);
        return n;
      }
      const n = currentIndex + 1;
      return (n >= queue.length) ? ((repeatMode === 'all') ? 0 : -1) : n;
    }
    function prevIndex(){
      if (!queue.length) return -1;
      if (shuffleOn) return nextIndex();
      const p = currentIndex - 1;
      return (p < 0) ? ((repeatMode === 'all') ? (queue.length - 1) : -1) : p;
    }

    // ===== Init =====
    showStatus('Ready to search soundtracks!');
    loadHome();
    switchTab('popular'); // default landing tab
    setTimeout(async ()=>{
      try{
        const r = await fetch(`${API_BASE}/`);
        if(r.ok) showStatus('✅ Connected to server'); else throw new Error();
      }catch(_){
        showStatus('❌ Cannot connect to server.', true);
      }
    }, 800);
  </script>
</body>
</html>
"""

# -------------------------- Routes ----------------------------------

@app.route('/')
def index():
    return Response(FRONTEND_HTML, mimetype='text/html')
@app.route('/home')
def home_sections():
    try:
        data = downloader.get_home_sections()
        return jsonify(data)
    except Exception as e:
        return jsonify({'popular': [], 'latest': [], 'error': str(e)}), 500

@app.route('/search', methods=['POST'])
def search_soundtracks():
    try:
        data = request.get_json(force=True)
        query = (data.get('query') or '').strip()
        if not query:
            return jsonify({'error': 'No search query provided'}), 400
        results = downloader.search(query)
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/album/<album_id>')
def get_album_info(album_id):
    try:
        info = downloader.get_soundtrack_info(album_id)
        if not info:
            return jsonify({'error': 'Album not found'}), 404
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def start_download():
    try:
        data = request.get_json(force=True)
        album_id = (data.get('album_id') or '').strip()
        output_path = normalize_output_path(data.get('output_path'))
        selected_tracks = data.get('selected_tracks', None)
        if not album_id:
            return jsonify({'error': 'No album ID provided'}), 400

        progress_id = f"download_{int(time.time() * 1000)}"
        active_downloads[progress_id] = True

        thread = threading.Thread(
            target=downloader.download_soundtrack,
            args=(album_id, output_path, selected_tracks, progress_id),
            daemon=True
        )
        thread.start()

        return jsonify({'status': 'started', 'progress_id': progress_id, 'message': 'Download started'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/progress/<progress_id>')
def get_progress(progress_id):
    if progress_id in download_progress:
        return jsonify(download_progress[progress_id])
    return jsonify({'error': 'Progress ID not found'}), 404

@app.route('/cancel/<progress_id>', methods=['POST'])
def cancel_download(progress_id):
    if progress_id in active_downloads:
        del active_downloads[progress_id]
        if progress_id in download_progress:
            download_progress[progress_id].update({'status': 'cancelled', 'message': 'Download cancelled'})
        return jsonify({'status': 'cancelled'})
    return jsonify({'error': 'Download not found'}), 404

@app.route('/quit', methods=['POST'])
def quit_app():
    shutdown = request.environ.get('werkzeug.server.shutdown')
    def _kill():
        try:
            if shutdown:
                shutdown()
            time.sleep(0.4)
        finally:
            os._exit(0)
    threading.Thread(target=_kill, daemon=True).start()
    return ('', 204)

# ---- new: audio stream proxy so the footer can play tracks ----
@app.route('/stream')
def stream_audio():
    page_url = request.args.get('p', '').strip()
    if not page_url:
        return jsonify({'error': 'missing param p'}), 400
    try:
        dl = downloader.get_download_link(page_url)
        if not dl:
            return jsonify({'error': 'could not resolve download link'}), 404

        # Pass Range to origin so the browser can seek
        range_header = request.headers.get('Range')
        headers = {}
        if range_header:
            headers['Range'] = range_header

        r = downloader.session.get(dl, headers=headers, stream=True, timeout=30)
        # 200 (full) or 206 (partial) are both fine; mirror origin status
        status = r.status_code if r.status_code in (200, 206) else 200
        mime = r.headers.get('Content-Type', 'audio/mpeg')

        def generate():
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        resp = Response(generate(), status=status, mimetype=mime)

        # Pass through important headers so the <audio> element can seek correctly
        for h in ['Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag', 'Last-Modified', 'Cache-Control']:
            v = r.headers.get(h)
            if v:
                resp.headers[h] = v

        # Ensure Accept-Ranges even if origin didn’t include it
        if 'Accept-Ranges' not in resp.headers:
            resp.headers['Accept-Ranges'] = 'bytes'

        # Reasonable default if origin didn’t specify caching
        resp.headers.setdefault('Cache-Control', 'no-store')

        return resp
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -------------------------- Entrypoint --------------------------------

if __name__ == "__main__":
    import threading, webbrowser, time, socket, logging, sys
    try:
        import ctypes
    except Exception:
        ctypes = None

    log_dir = os.path.join(os.getenv("LOCALAPPDATA", os.getcwd()), "GameSoundtracks")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    def find_free_port():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def run_server(port):
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    try:
        port = find_free_port()
        url = f"http://127.0.0.1:{port}/"

        t = threading.Thread(target=run_server, args=(port,), daemon=False)
        t.start()

        for _ in range(80):
            try:
                import urllib.request
                urllib.request.urlopen(url, timeout=0.2)
                break
            except Exception:
                time.sleep(0.1)

        webbrowser.open_new(url)
        t.join()

    except Exception as e:
        logging.exception("Fatal error at startup")
        if ctypes:
            try:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"Game Soundtracks failed to start:\n{e}\n\nSee log at:\n{log_file}",
                    "Game Soundtracks",
                    0,
                )
            except Exception:
                pass
        sys.exit(1)
