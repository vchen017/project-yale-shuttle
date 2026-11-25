"""The browser test's copies of what the pages load from a CDN (#160):
Bootstrap, its icons and MapLibre. The test hands the browser these instead
of the CDN's, so it never reaches the outside world; this fetches them once,
beforehand, as `playwright install` fetches the browser:

    python -m e2e.assets

They're the files the templates pin with an integrity hash, and each copy
is checked against that hash, so it's byte for byte what the site loads.
Nothing else is fetched: a file the templates don't pin isn't served.
"""

import base64
import hashlib
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / ".e2e-assets"  # gitignored
PINNED = re.compile(r'(?:href|src)="(https://cdn\.jsdelivr\.net/[^"]+)"\s+integrity="(sha384-[^"]+)"')


def pinned():
    """{url: integrity} for every CDN file a template pins with a hash."""
    found = {}
    for template in sorted((ROOT / "templates").rglob("*.html")):
        for url, integrity in PINNED.findall(template.read_text()):
            found[url] = integrity
    return found


def local(url):
    """The checked local copy of a pinned CDN file, or None."""
    integrity = pinned().get(url)
    path = _path(url)
    if integrity and path.exists() and _integrity(path.read_bytes()) == integrity:
        return path
    return None


def missing():
    """The pinned files without a checked local copy."""
    return [url for url in pinned() if local(url) is None]


def fetch():
    CACHE.mkdir(exist_ok=True)
    for url, integrity in pinned().items():
        if local(url):
            continue
        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read()
        if _integrity(content) != integrity:
            sys.exit(f"{url} doesn't match the hash the templates pin")
        _path(url).write_bytes(content)
        print(f"fetched {url}")


def _path(url):
    return CACHE / re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[1])


def _integrity(content):
    return "sha384-" + base64.b64encode(hashlib.sha384(content).digest()).decode()


if __name__ == "__main__":
    fetch()
