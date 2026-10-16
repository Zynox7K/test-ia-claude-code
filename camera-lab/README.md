# CamSim - Simulated Camera Security Lab (Educational)

A deliberately vulnerable, **fully simulated** IP-camera service plus a scanner/exploiter,
for security training and CTF-style practice only. Nothing here touches a real device:
the "shell" and "filesystem" are in-memory fakes.

## Vulnerabilities planted
1. **Command injection** in `POST /api/diag/ping` (`host` param) and `GET /api/firmware` (`version` param).
2. **Path traversal** in `GET /api/snapshot` and `GET /api/logs` (`file` param).
3. **Default / weak credentials** (`admin:admin`, `root:root`, `viewer:viewer`).
4. **Unauthenticated admin endpoint** `GET /api/admin/config` (and `/api/admin/users`).

## Run
```bash
cd camera-lab
python3 camera_server.py            # listens on :8080 (CAM_PORT to override)
python3 exploit.py --target http://127.0.0.1:8080 all
```

## Disclaimer
For educational use in an isolated lab only. Do not test against systems you do not own.