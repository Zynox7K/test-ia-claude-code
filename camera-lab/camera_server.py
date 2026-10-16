#!/usr/bin/env python3
"""CamSim - Simulated IP camera firmware (EDUCATIONAL LAB ONLY).

This is a deliberately vulnerable, fully simulated camera service used for
security training / CTF-style exercises. It does NOT control any real device
and does NOT execute real system commands: every "shell" interaction is faked
inside an in-memory sandbox. Do not point real attacks at systems you do not
own.

Vulnerabilities intentionally planted (for the scanner/exploit to find):
  1. Command injection in /api/diag/ping  (host parameter)
  2. Path traversal in /api/snapshot      (file parameter)
  3. Default / weak credentials          (admin:admin, root:root)
  4. Unauthenticated admin endpoint      (/api/admin/config)
"""
import http.server
import json
import os
import re
import socketserver
import urllib.parse

HOST = "0.0.0.0"
PORT = int(os.environ.get("CAM_PORT", "8080"))

# ---------------------------------------------------------------------------
# Simulated in-memory filesystem (what a real camera firmware might expose)
# ---------------------------------------------------------------------------
FAKE_FS = {
    "/etc/passwd": (
        "root:x:0:0:root:/root:/bin/sh\n"
        "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
        "bin:x:2:2:bin:/bin:/usr/sbin/nologin\n"
        "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
        "admin:x:1000:1000:admin:/home/admin:/bin/sh\n"
    ),
    "/etc/hostname": "camsim-lab-01\n",
    "/etc/shadow": "root:$6$rounds=5000$salt$hash:18000:0:99999:7:::\nadmin:$6$salt$hash:18000:0:99999:7:::\n",
    "/app/config.json": json.dumps(
        {
            "device_id": "CAM-0042",
            "admin_password": "admin",
            "stream_key": "sk_live_9f8a7b6c5d4e",
            "wifi_psk": "SuperSecretWifi!",
            "cloud_token": "tok_abc123xyz",
        },
        indent=2,
    ),
    "/app/firmware/version": "1.4.2\n",
    "/var/log/camera.log": (
        "2026-10-14T08:00:00Z boot ok\n"
        "2026-10-14T08:00:01Z stream started on port 554\n"
        "2026-10-14T08:00:02Z admin login from 192.168.1.5\n"
    ),
    "/tmp/snapshot_001.jpg": "[binary jpeg data placeholder]",
}

# ---------------------------------------------------------------------------
# Fake "shell" used to simulate command injection safely.
# Only a tiny whitelist of commands is implemented, returning canned output.
# ---------------------------------------------------------------------------
def _sim_cmd(name, args):
    if name == "ping":
        target = args[-1] if args else "127.0.0.1"
        return f"PING {target}: 56 data bytes\n64 bytes from {target}: icmp_seq=1 ttl=64 time=0.42 ms\n--- {target} ping statistics ---\n1 packets transmitted, 1 received, 0% packet loss"
    if name == "echo":
        return " ".join(args)
    if name == "id":
        return "uid=0(root) gid=0(root) groups=0(root)"
    if name == "whoami":
        return "root"
    if name == "hostname":
        return "camsim-lab-01"
    if name == "uname":
        return "Linux camsim-lab-01 5.10.0 #1 SMP aarch64 GNU/Linux"
    if name == "cat":
        path = args[0] if args else ""
        # normalize traversal
        norm = _normalize_path(path)
        if norm in FAKE_FS:
            return FAKE_FS[norm]
        return f"cat: {path}: No such file or directory"
    if name == "ls":
        path = args[0] if args and not args[0].startswith("-") else "/"
        norm = _normalize_path(path)
        entries = sorted({p for p in FAKE_FS if p.startswith(norm.rstrip("/") + "/") or p == norm})
        # top-level entries under norm
        prefix = norm if norm.endswith("/") else norm + "/"
        subs = sorted({p[len(prefix):].split("/")[0] for p in FAKE_FS if p.startswith(prefix)})
        return "  ".join(subs) if subs else ""
    if name == "pwd":
        return "/app"
    if name == "ps":
        return "PID TTY          TIME CMD\n  1 ?        00:00:01 init\n 42 ?        00:00:00 camera_daemon\n 99 ?        00:00:00 httpd"
    return f"sh: {name}: command not found"


def _normalize_path(p):
    # Resolve .. segments against a virtual root (vulnerable: no chroot enforcement)
    parts = []
    for seg in p.split("/"):
        if seg == "" or seg == ".":
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/" + "/".join(parts)


def fake_shell(cmd_str):
    """Simulate a vulnerable shell: splits on ; && | and runs each segment."""
    # Split on shell separators
    segments = re.split(r"\s*(?:;|&&|\|\|?|\n)\s*", cmd_str)
    out_lines = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # strip leading/trailing quotes
        tokens = seg.split()
        if not tokens:
            continue
        name = tokens[0]
        args = tokens[1:]
        try:
            res = _sim_cmd(name, args)
        except Exception as e:  # noqa: BLE001
            res = f"error: {e}"
        if res:
            out_lines.append(res)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class CameraHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CamSim/1.4.2"

    def _send(self, status, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, indent=2))

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode() or "{}")
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(200, {
                "device": "CamSim-0042",
                "model": "CS-2000",
                "firmware": "1.4.2",
                "uptime_s": 12345,
                "stream": "rtsp://camsim:554/live",
                "recording": True,
            })
            return
        if path == "/api/stream":
            self._send(200, "MJPEG stream placeholder (simulated)\n", "text/plain")
            return
        # VULN 2: path traversal
        if path == "/api/snapshot":
            name = (qs.get("file") or ["snapshot_001.jpg"])[0]
            target = _normalize_path("/snapshots/" + name)
            # intentionally no containment check
            if target in FAKE_FS:
                self._send(200, FAKE_FS[target], "text/plain")
            else:
                # also allow reading any fake path directly
                if name in FAKE_FS:
                    self._send(200, FAKE_FS[name], "text/plain")
                else:
                    self._json(404, {"error": "not found", "resolved": target})
            return
        if path == "/api/config":
            # weak: only checks a static token-ish header, easily bypassed
            token = self.headers.get("X-Auth-Token", "")
            if token == "admin":
                self._json(200, json.loads(FAKE_FS["/app/config.json"]))
            else:
                self._json(401, {"error": "unauthorized"})
            return
        # VULN 4: unauthenticated admin endpoint
        if path == "/api/admin/config":
            self._json(200, json.loads(FAKE_FS["/app/config.json"]))
            return
        if path == "/api/admin/users":
            self._json(200, [
                {"user": "admin", "role": "admin"},
                {"user": "viewer", "role": "viewer"},
                {"user": "root", "role": "root"},
            ])
            return
        if path == "/api/logs":
            name = (qs.get("file") or ["/var/log/camera.log"])[0]
            target = _normalize_path(name)
            if target in FAKE_FS:
                self._send(200, FAKE_FS[target], "text/plain")
            else:
                self._json(404, {"error": "not found", "resolved": target})
            return
        if path == "/api/firmware":
            ver = (qs.get("version") or ["1.4.2"])[0]
            # VULN 1 variant: command injection via version param into a shell cmd
            cmd = f"echo checking firmware {ver}"
            self._json(200, {"version": ver, "check_output": fake_shell(cmd)})
            return
        self._json(404, {"error": "unknown endpoint", "path": path})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == "/api/login":
            u = body.get("username") or body.get("user")
            p = body.get("password") or body.get("pass")
            # weak default creds
            if (u, p) in (("admin", "admin"), ("root", "root"), ("viewer", "viewer")):
                self._json(200, {"status": "ok", "token": "admin", "role": u})
            else:
                self._json(401, {"error": "bad credentials"})
            return
        # VULN 1: command injection
        if path == "/api/diag/ping":
            host = body.get("host", "127.0.0.1")
            # vulnerable: builds a shell command from user input
            cmd = f"ping -c 1 {host}"
            result = fake_shell(cmd)
            self._json(200, {"host": host, "output": result})
            return
        if path == "/api/diag/traceroute":
            host = body.get("host", "127.0.0.1")
            cmd = f"traceroute {host}"
            # traceroute not implemented -> falls to not found, but injection still evaluated
            result = fake_shell(f"echo traceroute {host}")
            self._json(200, {"host": host, "output": result})
            return
        if path == "/api/config":
            self._json(200, {"status": "updated", "note": "simulated"})
            return
        self._json(404, {"error": "unknown endpoint", "path": path})

    def log_message(self, *args):
        pass


INDEX_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>CamSim Camera</title></head>
<body style="font-family:sans-serif;background:#111;color:#eee;padding:2rem">
<h1>CamSim IP Camera</h1>
<p>Simulated device. Firmware 1.4.2</p>
<ul>
<li><a href="/api/status" style="color:#6cf">/api/status</a></li>
<li><a href="/api/snapshot?file=snapshot_001.jpg" style="color:#6cf">/api/snapshot</a></li>
<li><a href="/api/admin/config" style="color:#6cf">/api/admin/config</a> (unauth)</li>
</ul>
<p>Diagnostic ping: POST /api/diag/ping {"host":"..."}</p>
</body></html>
"""


def main():
    with socketserver.TCPServer((HOST, PORT), CameraHandler) as httpd:
        httpd.allow_reuse_address = True
        print(f"CamSim camera listening on http://{HOST}:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()