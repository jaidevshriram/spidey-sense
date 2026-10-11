"""One-time Oura login (OAuth2) for the cloud heart-rate source, if personal access tokens aren't available.

    1. https://cloud.ouraring.com/oauth/applications -> new app, redirect URI http://localhost:8099/callback
    2. put OURA_CLIENT_ID=... and OURA_CLIENT_SECRET=... in the repo-root .env
    3. python3 tools/oura_oauth.py      -> opens the Oura login in your browser; you log in and approve
The token is saved 0600 to ~/.config/spidey/oura_token.json (outside the repo) and refreshed automatically.
Nothing is printed except status lines; no secrets.
"""
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from spidey import heart as H  # noqa: E402

PORT, REDIRECT = 8099, "http://localhost:8099/callback"
SCOPES = "heartrate personal daily"


def main():
    env = H.read_env_file(ROOT / ".env")
    cid, sec = env.get("OURA_CLIENT_ID"), env.get("OURA_CLIENT_SECRET")
    if not (cid and sec):
        sys.exit("Set OURA_CLIENT_ID and OURA_CLIENT_SECRET in .env first (see the docstring).")
    state = secrets.token_urlsafe(16)
    done = threading.Event()
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                try:
                    tok = H.oura_token_request({"grant_type": "authorization_code", "code": q["code"][0],
                                                "redirect_uri": REDIRECT, "client_id": cid, "client_secret": sec})
                    H.save_oura_token(tok)
                    result["ok"] = True
                except Exception as e:
                    result["err"] = f"{type(e).__name__}"
            else:
                result["err"] = q.get("error", ["state mismatch / no code"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Spidey is connected to your Oura ring. You can close this tab." if result.get("ok") \
                else f"Oura login failed: {result.get('err')}"
            self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode())
            done.set()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "https://cloud.ouraring.com/oauth/authorize?" + urllib.parse.urlencode(
        {"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "scope": SCOPES, "state": state})
    print(f"Opening the Oura login. If it doesn't open, visit:\n{url}")
    webbrowser.open(url)
    done.wait(300)
    srv.shutdown()
    print("Saved token to ~/.config/spidey/oura_token.json" if result.get("ok") else f"Failed: {result.get('err', 'timed out')}")


if __name__ == "__main__":
    main()
