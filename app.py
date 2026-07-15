"""
Dubbing Studio V2 helper — UI + proxy for the ElevenLabs Dubbing Projects API.

Run locally:  python app.py            -> http://localhost:8377
Host online:  set env vars ELEVENLABS_API_KEY, APP_PASSWORD, PORT
              (or put them in a secrets.json next to this file:
               {"elevenlabs_api_key": "...", "app_password": "..."})

The browser UI calls this server, and this server forwards requests to
https://api.elevenlabs.io adding the xi-api-key header, so the key never
reaches the browser. When APP_PASSWORD is set, every API route requires the
x-app-password header. No third-party packages needed.
"""

import base64
import difflib
import hmac
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ELEVEN_BASE = "https://api.elevenlabs.io"
FROZEN = getattr(sys, "frozen", False)  # True when running as a PyInstaller exe
# bundled data files (index.html) live in the PyInstaller extraction dir
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
try:
    with open(os.path.join(HERE, "secrets.json"), encoding="utf-8") as f:
        _secrets = json.load(f)
    API_KEY = API_KEY or _secrets.get("elevenlabs_api_key", "")
    APP_PASSWORD = APP_PASSWORD or _secrets.get("app_password", "")
except FileNotFoundError:
    pass
try:
    # written by build_exe.py at build time; obfuscated, not encrypted
    from _embedded import K as _obf
    API_KEY = API_KEY or bytes(b ^ 0x5A for b in base64.b64decode(_obf)).decode("utf-8")
except ImportError:
    pass

PORT = int(os.environ.get("PORT", 8377))
# hosts (Render, Railway, ...) set PORT and need 0.0.0.0; local default stays loopback
HOST = os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")


def pick_free_port(preferred):
    """Use the preferred port, or walk upward if another instance holds it."""
    for port in range(preferred, preferred + 20):
        try:
            with socket.socket() as s:
                s.bind((HOST, port))
            return port
        except OSError:
            continue
    return preferred


def _norm_word(w):
    """Normalize a word for matching: strip punctuation, lowercase."""
    return re.sub(r"[^\w']+", "", w, flags=re.UNICODE).lower()


_TS = r"\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}"


def _strip_subtitle_markup(text):
    """Remove SRT/VTT structure (cue numbers, timecodes) from a pasted transcript.

    Handles both well-formed multi-line subtitle files and 'flowed' pastes where
    line breaks were lost and counters/timestamps sit inline between words.
    """
    # inline unit: optional cue counter + "HH:MM:SS,mmm --> HH:MM:SS,mmm"
    text = re.sub(rf"(?:(?<=\s)|^)\d{{1,4}}\s+{_TS}\s*-->\s*{_TS}", " ", text)
    text = re.sub(rf"{_TS}\s*-->\s*{_TS}", " ", text)
    text = re.sub(_TS, " ", text)  # stray leftover timecodes
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.upper().startswith("WEBVTT") or s.isdigit():
            continue  # blank lines, VTT header, cue counters on their own line
        lines.append(s)
    return "\n".join(lines)


def align_transcript(segments, proofread):
    """
    Redistribute a proofread transcript into ElevenLabs' segment boundaries.

    Both texts are the same speech, so a word-level diff lines them up. Each
    ASR segment covers a span of ASR words; the matching span of proofread
    words (original casing/punctuation preserved) becomes its corrected text.
    Segments whose boundaries fall inside a mismatched region are flagged for
    manual review instead of being trusted blindly.
    """
    proofread = _strip_subtitle_markup(proofread)
    proof_raw = proofread.split()
    proof_norm = [_norm_word(w) for w in proof_raw]

    asr_norm = []
    seg_spans = []  # (start, end) word-index span of each segment in asr_norm
    for seg in segments:
        words = seg["text"].split()
        start = len(asr_norm)
        asr_norm.extend(_norm_word(w) for w in words)
        seg_spans.append((start, len(asr_norm)))

    sm = difflib.SequenceMatcher(None, asr_norm, proof_norm, autojunk=False)
    n = len(asr_norm)
    a2b = [0] * (n + 1)     # ASR word index -> proofread word index
    exact = [False] * (n + 1)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                a2b[k] = j1 + (k - i1)
                exact[k] = True
        elif tag in ("replace", "delete"):
            for k in range(i1, i2):
                # spread replaced words proportionally; deleted words collapse
                frac = (k - i1) * (j2 - j1) / max(1, i2 - i1)
                a2b[k] = j1 + round(frac)
                exact[k] = False
        # 'insert': no ASR index advances; inserted proofread words fall into
        # the span that ends at the next mapped boundary.
    a2b[n] = len(proof_norm)
    exact[n] = True
    if n:
        a2b[0] = 0  # leading proofread words the ASR missed join segment 1

    results = []
    for seg, (s, e) in zip(segments, seg_spans):
        new_text = " ".join(proof_raw[a2b[s]:a2b[e]])
        changed = new_text != seg["text"]
        flagged = changed and (not exact[s] or not exact[e] or not new_text)
        results.append({
            "id": seg["id"],
            "old_text": seg["text"],
            "new_text": new_text,
            "changed": changed,
            "flagged": flagged,
        })
    return results


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- routing ----------

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_index()
        elif self.path == "/config":
            self._send(200, json.dumps({
                "password_required": bool(APP_PASSWORD),
                "server_key": bool(API_KEY),
            }).encode("utf-8"))
        elif self.path == "/auth/check":
            if self._authorized():
                self._send(200, b'{"ok":true}')
        elif self.path.startswith("/audio?"):
            if self._authorized():
                self._proxy_audio()
        elif self.path.startswith("/api/"):
            if self._authorized():
                self._proxy_api()
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        self._route_api()

    def do_PATCH(self):
        self._route_api()

    def do_DELETE(self):
        self._route_api()

    def _route_api(self):
        if not self._authorized():
            return
        if self.path == "/align":
            self._handle_align()
        elif self.path.startswith("/api/"):
            self._proxy_api()
        else:
            self._send(404, b'{"error":"not found"}')

    def _authorized(self):
        """Shared-password gate. Sends the 401 itself when access is denied."""
        if not APP_PASSWORD:
            return True
        supplied = self.headers.get("x-app-password", "")
        if hmac.compare_digest(supplied, APP_PASSWORD):
            return True
        time.sleep(0.5)  # slow down brute-force guessing
        self._send(401, b'{"error":"unauthorized","detail":"Access password missing or wrong."}')
        return False

    def _handle_align(self):
        """Local endpoint: match a proofread transcript to ASR segmentation."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length))
            results = align_transcript(payload["segments"], payload["proofread"])
            self._send(200, json.dumps({"segments": results}).encode("utf-8"))
        except Exception as e:
            self._send(400, json.dumps({"error": f"align failed: {e}"}).encode("utf-8"))

    # ---------- handlers ----------

    def _serve_index(self):
        try:
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self._send(200, body, "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send(500, b"index.html not found next to app.py")

    def _proxy_api(self):
        """Forward /api/v1/... to https://api.elevenlabs.io/v1/... verbatim."""
        url = ELEVEN_BASE + self.path[len("/api"):]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        req = urllib.request.Request(url, data=body, method=self.command)
        # server-side key wins; the client header is only a local-dev fallback
        api_key = API_KEY or self.headers.get("x-api-key", "")
        req.add_header("xi-api-key", api_key)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)

        try:
            # generous timeout: file uploads to ElevenLabs can take minutes
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read()
                self._send(resp.status, data, resp.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            data = e.read() or json.dumps({"error": str(e)}).encode()
            self._send(e.code, data, e.headers.get("Content-Type", "application/json"))
        except Exception as e:  # network failure etc.
            self._send(502, json.dumps({"error": f"proxy failure: {e}"}).encode())

    def _proxy_audio(self):
        """Stream a signed output URL so the browser can download it with a filename."""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        url = (qs.get("url") or [""])[0]
        name = (qs.get("name") or ["dub_output.wav"])[0]
        if not url.startswith("https://"):
            self._send(400, b'{"error":"bad url"}')
            return
        try:
            with urllib.request.urlopen(url, timeout=300) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "audio/wav"))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send(502, json.dumps({"error": f"audio fetch failed: {e}"}).encode())

    # ---------- helpers ----------

    def _send(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        # keep the console readable: only log API traffic, not static files
        if self.path.startswith("/api/"):
            print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


if __name__ == "__main__":
    port = PORT if "PORT" in os.environ else pick_free_port(PORT)
    server = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://{'localhost' if HOST == '127.0.0.1' else HOST}:{port}"
    print(f"Dubbing Studio V2 running at {url}")
    print(f"API key: {'built in' if API_KEY else 'NOT set - browser must supply one'}")
    print(f"Password gate: {'ON' if APP_PASSWORD else 'off (local mode)'}")
    print("Close this window (or press Ctrl+C) to stop.")
    if FROZEN and "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
