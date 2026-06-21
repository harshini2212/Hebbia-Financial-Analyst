"""Serve ./public locally the way Vercel will (applies the same /api rewrites).
For verifying the static export before deploy:  python scripts/serve_static.py --port 8060
"""

import http.server
import os
import sys
from functools import partial

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8060
DIR = os.path.join(os.path.dirname(__file__), "..", "public")


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/api/filings", "/api/registry", "/api/health", "/api/benchmark", "/api/grid"):
            self.path = p + ".json"
        elif ((p.startswith("/api/analysis/") or p.startswith("/api/qoe/")
               or p.startswith("/api/sources/")) and not p.endswith(".json")):
            self.path = p + ".json"
        elif p == "/":
            self.path = "/index.html"
        return super().do_GET()


print(f"static preview -> http://127.0.0.1:{PORT}")
http.server.HTTPServer(("127.0.0.1", PORT),
                       partial(Handler, directory=DIR)).serve_forever()
