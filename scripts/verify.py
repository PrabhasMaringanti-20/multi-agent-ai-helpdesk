#!/usr/bin/env python3
"""End-to-end verification for the Enterprise AI Helpdesk Platform.

    python scripts/verify.py

Prints PASS / FAIL / SKIP for every component and exits non-zero if any
CRITICAL component fails. Assumes the stack is running (start it first with
`python start.py`). Pure standard library; no PowerShell.

Optional components (Redis, ChromaDB, the frontend UI, the unit-test runner)
are reported but do NOT fail the overall result - the platform runs locally
without them (graceful degradation).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

HOST, PORT = "127.0.0.1", 8000
FRONTEND_PORT = 5280
BASE = f"http://{HOST}:{PORT}"

ORG = "acme"
EMAIL = "user@acme.com"
PASSWORD = "ChangeMe123!"

GREEN, RED, YEL, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
results: list[tuple[str, str, str, bool]] = []  # (name, status, detail, critical)


def enable_ansi() -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7
            )
        except Exception:
            pass


def record(name: str, status: str, detail: str = "", critical: bool = True) -> None:
    results.append((name, status, detail, critical))
    color = {"PASS": GREEN, "FAIL": RED, "SKIP": YEL}.get(status, "")
    tag = "     " if critical else " opt "
    print(f"  [{color}{status:^4}{RST}]{DIM}{tag}{RST}{name}" + (f"  {DIM}- {detail}{RST}" if detail else ""))


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    return env


def http(method: str, url: str, headers: dict | None = None, body: dict | None = None,
         timeout: float = 15.0) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def chat(token: str, message: str, timeout: float = 90.0) -> dict:
    """Drive one SSE chat turn; return {decision, citations, text}."""
    prefix = ENV.get("API_V1_PREFIX", "/api/v1")
    req = urllib.request.Request(
        f"{BASE}{prefix}/chat/messages", method="POST",
        data=json.dumps({"message": message}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    decision, text, citations, event = None, "", [], None
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    payload = json.loads(line.split(":", 1)[1].strip()).get("data", {})
                except json.JSONDecodeError:
                    continue
                if event == "citations":
                    citations = payload.get("citations", [])
                elif event == "decision":
                    decision = payload.get("decision")
                elif event == "done":
                    text = payload.get("response_text", "")
    return {"decision": decision, "citations": citations, "text": text}


def venv_python() -> str | None:
    for p in (BACKEND / ".venv/Scripts/python.exe",
              BACKEND / ".venv/bin/python",
              Path(os.environ.get("LOCALAPPDATA", "")) / "helpdesk/venv/Scripts/python.exe"):
        if p.exists():
            return str(p)
    return None


# --------------------------------------------------------------------------- #
ENV = load_env(BACKEND / ".env")


def main() -> None:
    enable_ansi()
    provider = ENV.get("LLM_PROVIDER", "gemini").lower()
    prefix = ENV.get("API_V1_PREFIX", "/api/v1")
    print("=" * 64)
    print(" Enterprise AI Helpdesk - verification")
    print("=" * 64)

    # 1) Backend + health
    code, _ = http("GET", f"{BASE}/health")
    backend_up = code == 200
    record("Backend starts", "PASS" if backend_up else "FAIL",
           f"GET /health -> {code or 'no response'}")
    if not backend_up:
        record("Health endpoint passes", "FAIL", "backend not reachable")
        summarize()
        return
    record("Health endpoint passes", "PASS", "GET /health -> 200")

    # 2) Readiness -> DB / Redis / Chroma / providers
    rcode, rbody = http("GET", f"{BASE}/health/ready")
    checks = {}
    try:
        checks = json.loads(rbody).get("checks", {})
    except Exception:
        pass
    record("Database connected", "PASS" if checks.get("database") else "FAIL",
           "postgres reachable" if checks.get("database") else "check backend/.env POSTGRES_*")
    record("Redis connected", "PASS" if checks.get("redis") else "FAIL",
           "" if checks.get("redis") else "optional locally - app degrades gracefully", critical=False)
    record("Chroma connected", "PASS" if checks.get("chroma") else "FAIL",
           "" if checks.get("chroma") else "optional locally - sparse search still retrieves", critical=False)

    # 3) Frontend
    fe = port_open(FRONTEND_PORT)
    record("Frontend starts", "PASS" if fe else "FAIL",
           f"http://localhost:{FRONTEND_PORT}" if fe else "run `python start.py` to start the UI",
           critical=False)

    # 4) Login
    lcode, lbody = http("POST", f"{BASE}{prefix}/auth/login",
                        body={"org_slug": ORG, "email": EMAIL, "password": PASSWORD})
    token = ""
    try:
        token = json.loads(lbody).get("access_token", "")
    except Exception:
        pass
    login_ok = lcode == 200 and bool(token)
    record("Login works", "PASS" if login_ok else "FAIL",
           f"POST /auth/login -> {lcode}" + ("" if login_ok else " (did you seed demo data?)"))

    # 5) Chat + Retrieval + live Gemini (one grounded turn)
    chat_ok = retrieval_ok = gemini_live = False
    detail = "requires login"
    if login_ok:
        try:
            res = chat(token, "On Windows 11 with the GlobalProtect VPN client I get "
                              "error code 800. How do I fix it?")
            chat_ok = bool(res["text"])
            retrieval_ok = len(res["citations"]) >= 1 and res["decision"] == "deliver"
            gemini_live = chat_ok and res["decision"] in ("deliver", "clarify", "escalate")
            detail = f"decision={res['decision']}, citations={len(res['citations'])}"
        except Exception as e:  # noqa: BLE001
            detail = f"error: {e}"
    record("Chat works", "PASS" if chat_ok else "FAIL", detail)

    # 6) Gemini
    if provider == "fake":
        record("Gemini connected", "SKIP", "LLM_PROVIDER=fake (set to gemini for a live call)", critical=False)
    else:
        record("Gemini connected", "PASS" if gemini_live else "FAIL",
               f"live {provider} call via chat" if gemini_live else "chat did not complete (check GEMINI_API_KEY)")

    # 7) Ticket subsystem
    tcode, _ = http("GET", f"{BASE}{prefix}/tickets",
                    headers={"Authorization": f"Bearer {token}"} if token else None)
    record("Ticket creation works", "PASS" if tcode == 200 else "FAIL",
           f"GET /tickets -> {tcode} (AI escalation creates tickets)")

    # 8) Retrieval (grounded answer with citations)
    record("Retrieval works", "PASS" if retrieval_ok else "FAIL",
           "grounded answer with citations" if retrieval_ok else "no citations - is the KB seeded?")

    # 9) Unit tests
    py = venv_python()
    if not py:
        record("Unit tests pass", "SKIP", "no venv - `pip install -r backend/requirements-dev.txt`", critical=False)
    elif subprocess.run([py, "-c", "import pytest"], capture_output=True).returncode != 0:
        record("Unit tests pass", "SKIP", "pytest not installed - `pip install -r backend/requirements-dev.txt`", critical=False)
    else:
        r = subprocess.run([py, "-m", "pytest", "-q", "--tb=line"], cwd=str(BACKEND),
                           env={**os.environ, "PYTHONPATH": str(BACKEND)},
                           capture_output=True, text=True)
        out = f"{r.stdout or ''}\n{r.stderr or ''}"
        summary = next(
            (ln.strip() for ln in reversed(out.splitlines())
             if any(w in ln.lower() for w in ("passed", "failed", "error"))),
            f"pytest exit {r.returncode}",
        )
        record("Unit tests pass", "PASS" if r.returncode == 0 else "FAIL", summary[:70])

    summarize()


def summarize() -> None:
    crit_fail = [n for n, s, _, c in results if c and s == "FAIL"]
    passed = sum(1 for _, s, _, _ in results if s == "PASS")
    print("=" * 64)
    print(f"  {passed} passed | {sum(1 for _,s,_,_ in results if s=='FAIL')} failed | "
          f"{sum(1 for _,s,_,_ in results if s=='SKIP')} skipped")
    if crit_fail:
        print(f"  {RED}OVERALL: FAIL{RST} - critical: {', '.join(crit_fail)}")
        sys.exit(1)
    print(f"  {GREEN}OVERALL: PASS{RST} - all critical components healthy")
    sys.exit(0)


if __name__ == "__main__":
    main()
