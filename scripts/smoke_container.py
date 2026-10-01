"""Validate the built image without any user credentials or persistent data."""
import json
import re
import time
from http.client import HTTPException
from urllib.request import urlopen

BASE = "http://127.0.0.1:8000"
VERSION = "2.52.0"
REFERENCE = "desktop-v3.12.0rc52-20261001"


def get(path):
    with urlopen(BASE + path, timeout=5) as response:
        assert response.status == 200
        return response.read().decode("utf-8")


for attempt in range(30):
    try:
        health = json.loads(get("/api/health"))
        break
    except (OSError, HTTPException):
        if attempt == 29:
            raise
        time.sleep(2)

assert health["status"] == "ok", health
assert health["version"] == VERSION, health
assert health["reference"] == REFERENCE, health
reference = json.loads(get("/api/reference"))
assert reference["id"] == REFERENCE, reference
assert reference["web_api_version"] == VERSION, reference
html = get("/")
assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', html)
assert len(assets) >= 2, html
javascript = []
for asset in assets:
    content = get(asset)
    assert content
    if asset.endswith(".js"):
        javascript.append(content)
assert len(javascript) == 1
assert VERSION in javascript[0]
print(f"Validated health, reference and static assets: {VERSION} / {REFERENCE}")
