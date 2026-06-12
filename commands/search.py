import webbrowser
import urllib.parse


def web_search(query: str) -> str:
    url = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}"
    webbrowser.open(url)
    return f"Searching for {query}"


def go_to_site(destination: str) -> str:
    url = destination.strip()

    # Handle spoken punctuation: "youtube dot com slash watch" etc.
    url = url.replace(" dot ", ".").replace(" slash ", "/").replace(" dash ", "-")

    # If it still has spaces and no dot, it's ambiguous — fall back to search
    if " " in url and "." not in url:
        return web_search(url)

    # Bare name with no TLD → assume .com
    if "." not in url:
        url += ".com"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    webbrowser.open(url)
    return f"Opening {destination}"
