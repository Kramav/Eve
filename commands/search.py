import re
import urllib.parse
import urllib.request
import webbrowser

import core.session as _sess_mod
from core.session import Mode


# ── DuckDuckGo scraper ─────────────────────────────────────────────────────

def _fetch_search_results(query: str, n: int = 5) -> list:
    """Return up to *n* results as [{title, url, domain}] from DDG HTML search."""
    try:
        url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote_plus(query)
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        results = []
        # Two-step: find every <a> tag then check for result__a class.
        # Attribute order varies (href may come before class), so we can't
        # rely on a fixed-order regex.
        for tag_m in re.finditer(r'<a\s([^>]*)>(.*?)</a>', html, re.S | re.I):
            attrs   = tag_m.group(1)
            content = tag_m.group(2)
            if 'result__a' not in attrs:
                continue
            href_m = re.search(r'href="([^"]*)"', attrs)
            if not href_m:
                continue
            uddg = re.search(r'uddg=([^&"]+)', href_m.group(1))
            if not uddg:
                continue
            title = re.sub(r'<[^>]+>', '', content).strip()
            title = re.sub(r'\s+', ' ', title)
            if not title:
                continue
            actual_url = urllib.parse.unquote(uddg.group(1))
            if not actual_url.startswith('http'):
                continue
            domain = re.sub(r'^https?://(www\.)?', '', actual_url).split('/')[0]
            results.append({'title': title, 'url': actual_url, 'domain': domain})
            if len(results) >= n:
                break

        print(f"[search] DDG returned {len(results)} results for '{query}'")
        return results
    except Exception as e:
        print(f"[search] DDG fetch failed: {e}")
        return []


# ── Intent handlers ────────────────────────────────────────────────────────

def web_search(query: str) -> str:
    """Direct DDG search — fallback when result list fetch fails."""
    url = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}"
    webbrowser.open(url)
    return f"Searching for {query}"


def web_search_list(query: str):
    """Fetch top results and show a pick-list in the overlay."""
    from core.response import SiteList
    results = _fetch_search_results(query)
    if not results:
        return web_search(query)

    sess = _sess_mod.get()
    sess.site_list  = results
    sess.video_list = []
    sess.mode = Mode.LISTING
    return SiteList(results, message=f'Results for "{query}"')


def go_to_site(destination: str) -> str:
    url = destination.strip()

    url = url.replace(" dot ", ".").replace(" slash ", "/").replace(" dash ", "-")

    if " " in url and "." not in url:
        return web_search(url)

    if "." not in url:
        url += ".com"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    webbrowser.open(url)
    return f"Opening {destination}"


# ── Site list selection ────────────────────────────────────────────────────

def select_site(n: int) -> str:
    sess = _sess_mod.get()
    if not sess.site_list:
        return "No search results are active."
    if n < 1 or n > len(sess.site_list):
        return f"Say a number between 1 and {len(sess.site_list)}."
    site = sess.site_list[n - 1]
    _sess_mod.reset()
    webbrowser.open(site['url'])
    return f"Opening {site['title']}"


def select_site_by_title(partial: str) -> str:
    sess = _sess_mod.get()
    pl = partial.lower()
    for i, s in enumerate(sess.site_list):
        if pl in s['title'].lower() or pl in s['domain'].lower():
            return select_site(i + 1)
    return f'No match for "{partial}". Try saying the number.'


def read_site_list() -> str:
    sess = _sess_mod.get()
    if not sess.site_list:
        return "No search results to read."
    parts = [f"{i}. {s['title']} from {s['domain']}"
             for i, s in enumerate(sess.site_list, 1)]
    return ". ".join(parts)
