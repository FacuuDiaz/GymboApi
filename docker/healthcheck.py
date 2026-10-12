"""
Container health check: GET /health/ready/ (process up + database reachable).
Uses only the standard library — no curl in the image. Sends the first
allowed host as Host header so production's ALLOWED_HOSTS check passes.
"""
import os
import sys
import urllib.request

host = (os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0].strip() or "localhost").lstrip(".")
request = urllib.request.Request("http://127.0.0.1:8000/health/ready/", headers={"Host": host})
try:
    with urllib.request.urlopen(request, timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
