import concurrent.futures
import html as _html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import core.session as _sess_mod
from core.session import Mode

# A full, current browser UA — the bare "AppleWebKit" string we used before now
# trips DuckDuckGo's bot detection and returns a results-less page.
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

_SETTINGS_FILE = Path(__file__).parent.parent / 'settings.json'


def _with_deadline(fn, *args, seconds: float = 6.0, default=None):
    """Run *fn* in a worker thread and abandon it if it exceeds *seconds*.

    Guarantees the caller never blocks longer than *seconds* even if the network
    call stalls in a way urllib's socket timeout doesn't cover (e.g. a hung DNS
    lookup). The abandoned thread keeps running but is daemon-bounded by the
    request's own timeout, so it reaps itself shortly after.
    ponytail: Python can't kill the thread; the per-request timeout caps the leak.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(fn, *args)
    pool.shutdown(wait=False)
    try:
        return fut.result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        return default


def brave_key() -> str:
    """Resolve the Brave Search API key. Priority: settings.json (written by
    the API Keys UI panel) → BRAVE_API_KEY env var (config.py). Empty if unset."""
    try:
        data = json.loads(_SETTINGS_FILE.read_text())
        k = (data.get('api_keys') or {}).get('brave') or ''
        if k.strip():
            return k.strip()
    except Exception:
        pass
    try:
        from config import BRAVE_API_KEY
        return (BRAVE_API_KEY or '').strip()
    except Exception:
        return ''


# ── DuckDuckGo scraper ─────────────────────────────────────────────────────

def _parse_results(page: str, n: int) -> list:
    """Extract [{title, url, domain}] from a DDG results page (lite or html).

    Handles both link styles: the lite/html `//duckduckgo.com/l/?uddg=<target>`
    redirect and direct `result__a` hrefs. Skips DuckDuckGo's own ad/help links.
    """
    results, seen = [], set()
    for m in re.finditer(r'<a\s([^>]*?)>(.*?)</a>', page, re.S | re.I):
        attrs, content = m.group(1), m.group(2)
        href_m = re.search(r'href="([^"]*)"', attrs)
        if not href_m:
            continue
        href = href_m.group(1)
        uddg = re.search(r'uddg=([^&"]+)', href)
        if uddg:
            actual_url = urllib.parse.unquote(uddg.group(1))
        elif 'result__a' in attrs and href.startswith('http'):
            actual_url = href            # html endpoint sometimes links directly
        else:
            continue
        if not actual_url.startswith('http'):
            continue
        domain = re.sub(r'^https?://(www\.)?', '', actual_url).split('/')[0]
        if domain.endswith('duckduckgo.com'):
            continue                     # ads / "more info" / internal links
        # Strip tags, then decode HTML entities (&amp;, &#x27;, …).
        title = _html.unescape(re.sub(r'<[^>]+>', '', content)).strip()
        title = re.sub(r'\s+', ' ', title)
        if not title or actual_url in seen:
            continue
        seen.add(actual_url)
        results.append({'title': title, 'url': actual_url, 'domain': domain})
        if len(results) >= n:
            break
    return results


def _fetch_search_results(query: str, n: int = 5) -> list:
    """Return up to *n* results as [{title, url, domain}] from DuckDuckGo.

    The old html.duckduckgo.com GET scraper is now blocked (returns an
    anomaly page with no results). We try the *lite* endpoint first, then
    fall back to an html POST. If DDG throttles both, returns [] and the
    caller opens the browser instead.
    """
    q = urllib.parse.quote_plus(query)
    attempts = [
        ('lite',  'https://lite.duckduckgo.com/lite/?q=' + q, None),
        ('html',  'https://html.duckduckgo.com/html/',
                  urllib.parse.urlencode({'q': query}).encode()),
    ]
    for name, url, data in attempts:
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': _UA})
            with urllib.request.urlopen(req, timeout=5) as resp:
                page = resp.read().decode('utf-8', errors='ignore')
            results = _parse_results(page, n)
            if results:
                print(f"[search] DDG/{name} returned {len(results)} results for '{query}'")
                return results
            print(f"[search] DDG/{name} returned no results for '{query}' (throttled?)")
        except Exception as e:
            print(f"[search] DDG/{name} fetch failed: {e}")
    return []


# ── Brave Search API (fallback) ─────────────────────────────────────────────

def _fetch_brave_results(query: str, n: int = 5, key: str = None) -> list:
    """Query the Brave Search API. Returns [{title, url, domain}] or [].

    Only called when DuckDuckGo yields nothing, to conserve the free-tier
    monthly quota. No-op (returns []) when no key is configured. Pass *key*
    explicitly to validate a candidate key (used by the API Keys panel test).
    """
    api_key = (key or brave_key()).strip()
    if not api_key:
        return []
    url = ('https://api.search.brave.com/res/v1/web/search?count='
           + str(n) + '&q=' + urllib.parse.quote_plus(query))
    req = urllib.request.Request(url, headers={
        'Accept':                'application/json',
        'Accept-Encoding':       'gzip',
        'X-Subscription-Token':  api_key,
        'User-Agent':            _UA,
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            import gzip
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode('utf-8', errors='ignore'))

    results = []
    for item in (data.get('web', {}).get('results') or [])[:n]:
        actual_url = item.get('url', '')
        title = _html.unescape(item.get('title', '')).strip()
        if not actual_url.startswith('http') or not title:
            continue
        domain = re.sub(r'^https?://(www\.)?', '', actual_url).split('/')[0]
        results.append({'title': title, 'url': actual_url, 'domain': domain})
    print(f"[search] Brave returned {len(results)} results for '{query}'")
    return results


def _fetch_brave_safe(query: str, n: int = 5) -> list:
    """Exception-swallowing wrapper for the fallback path."""
    try:
        return _fetch_brave_results(query, n)
    except Exception as e:
        print(f"[search] Brave fetch failed: {e}")
        return []


def test_brave_key(key: str = None) -> dict:
    """Validate a Brave key with a cheap query. Returns {ok, message}.
    Used by the API Keys panel so users get immediate feedback."""
    api_key = (key or brave_key()).strip()
    if not api_key:
        return {'ok': False, 'message': 'No key provided.'}
    try:
        results = _fetch_brave_results('test', n=1, key=api_key)
        return {'ok': True, 'message': 'Key works — Brave search is ready.'}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {'ok': False, 'message': 'Key rejected (invalid or unauthorized).'}
        if e.code == 429:
            return {'ok': False, 'message': 'Rate limited / quota exhausted.'}
        return {'ok': False, 'message': f'Brave error: HTTP {e.code}.'}
    except Exception as e:
        return {'ok': False, 'message': f'Could not reach Brave: {e}'}


# ── Intent handlers ────────────────────────────────────────────────────────

def web_search(query: str) -> str:
    """Open a DDG search in the chosen browser, surfaced beside the task without
    stealing focus (core.browser). Fallback when the in-app result list is off or
    a fetch fails."""
    from core import browser
    url = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}"
    browser.open_url(url)
    return f"Searching for {query}"


def web_search_list(query: str):
    """Fetch top results and show a pick-list in the overlay.

    DuckDuckGo first; Brave API only if DDG comes back empty (it throttles
    scrapers); browser as a last resort.
    """
    from core.response import SiteList
    from core import features
    # In-app DDG results are an alpha feature; when off, open the search in the
    # chosen browser (focus-safe via core.browser).
    if not features.get('inapp_search'):
        return web_search(query)
    # Hard 6s cap: a throttled/stalled DDG can never hang the assistant on
    # "Thinking" — bail to the browser fallback instead.
    results = _with_deadline(_fetch_search_results, query, seconds=6.0) or []
    has_brave = bool(brave_key())
    if not results and has_brave:
        results = _fetch_brave_safe(query)        # Brave only if a key is set
    if not results:
        # Nothing came back — open the browser so the user still gets results,
        # and if no Brave key is configured, nudge them toward setting one up.
        web_search(query)
        if not has_brave:
            return ("DuckDuckGo didn't return anything and no Brave Search key "
                    "is set up. I opened the results in your browser. For in-app "
                    "results, say 'open API keys' and add a free Brave Search key.")
        return f"Couldn't fetch results for {query}, so I opened your browser."

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

    from core import browser
    browser.open_url(url)
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
    from core import browser
    browser.open_url(site['url'])
    return f"Opening {site['title']}"


# ── Chosen browser ─────────────────────────────────────────────────────────

def set_web_browser(text: str) -> str:
    """Voice: 'use chrome as my browser' / 'set my browser to firefox' /
    'open searches in edge' → persist the chosen browser (core.browser)."""
    from core import browser
    t = (text or '').lower()
    key = None
    for name in ('firefox', 'chrome', 'edge', 'brave'):
        if name in t:
            key = name
            break
    if key is None and re.search(r'\bdefault\b', t):
        key = 'default'
    if key is None:
        return ("Which browser? I know Firefox, Chrome, Edge, Brave, or your "
                "default browser.")
    ok, label = browser.set_browser(key)
    if not ok:
        return label
    return f"Okay, I'll open links in {label}."


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
