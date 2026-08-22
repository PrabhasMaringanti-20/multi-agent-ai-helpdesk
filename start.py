#!/usr/bin/env python3
"""One-command launcher for the Enterprise AI Helpdesk Platform.

    python start.py              # start database + backend + frontend
    python start.py --stop       # stop backend, frontend, bundled database
    python start.py --seed-only  # only (re)create schema + demo data, then exit
    python start.py --no-frontend
    python start.py --docker     # bring the DB (and redis/chroma) up via docker compose

Pure standard library, so it runs with a plain `python` (no venv, no pip needed
to *launch*). It does NOT use PowerShell. It adapts to what is available:

  Database   : an already-running Postgres on :5432  ->  reuse it
               else `--docker`/Docker present         ->  docker compose up db
               else a bundled Postgres in %LOCALAPPDATA%\\helpdesk -> start it
  Backend py : backend/.venv  ->  %LOCALAPPDATA%\\helpdesk\\venv  ->  create backend/.venv

Redis and ChromaDB are OPTIONAL for a local run: the app degrades gracefully
(rate-limiting fails open, dense vector search is skipped and sparse full-text
search still retrieves). Use --docker to run them too.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
LOGS = ROOT / "logs"

BACKEND_HOST, BACKEND_PORT = "127.0.0.1", 8000
FRONTEND_PORT = 5280

# Bundled, durable local stack (created by the persistent-setup step).
STACK = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "helpdesk"
PG_EXE = STACK / "pgsql" / "bin" / "postgres.exe"
PG_CTL = STACK / "pgsql" / "bin" / "pg_ctl.exe"
PG_ISREADY = STACK / "pgsql" / "bin" / "pg_isready.exe"
PGDATA = STACK / "pgdata"
DURABLE_VENV_PY = STACK / "venv" / "Scripts" / "python.exe"

IS_WIN = os.name == "nt"
DETACHED = 0x00000008 | 0x00000200 if IS_WIN else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP

DEMO_USERS = [
    ("admin@acme.com", "admin"),
    ("engineer@acme.com", "support engineer"),
    ("sme@acme.com", "SME reviewer"),
    ("user@acme.com", "end user"),
]
DEMO_PASSWORD = "ChangeMe123!"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def say(msg: str, mark: str = "..") -> None:
    print(f"[ {mark:^4} ] {msg}", flush=True)


def ok(msg: str) -> None:
    say(msg, "OK")


def warn(msg: str) -> None:
    say(msg, "WARN")


def die(msg: str) -> None:
    say(msg, "STOP")
    sys.exit(1)


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.split("#", 1)[0].strip().strip('"').strip("'")
    return env


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if port_open(host, port):
            return True
        time.sleep(0.5)
    return False


def wait_for_pg_ready(host: str, port: int, timeout: float) -> bool:
    """Wait until Postgres can actually serve queries, not just accept TCP.

    Postgres opens its port during startup/recovery but rejects queries with
    "the database system is starting up" until ready; ``pg_isready`` exits 0
    only once the server is accepting connections. If the probe is unavailable
    we return True (the caller has already waited for the port to open).
    """
    if not PG_ISREADY.exists():
        return True
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = subprocess.run(
            [str(PG_ISREADY), "-h", host, "-p", str(port), "-U", "postgres"],
            capture_output=True,
        )
        if r.returncode == 0:
            return True
        time.sleep(0.7)
    return False


def http_get(url: str, timeout: float = 3.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status
    except Exception:
        return None


def wait_for_http(url: str, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if http_get(url) == 200:
            return True
        time.sleep(0.7)
    return False


def netstat_pids(port: int) -> list[int]:
    """PIDs listening on a TCP port (Windows netstat; no PowerShell)."""
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=15
        ).stdout
    except Exception:
        return []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                try:
                    pids.add(int(parts[4]))
                except ValueError:
                    pass
    return list(pids)


def kill_port(port: int) -> None:
    for pid in netstat_pids(port):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
            ok(f"stopped PID {pid} on :{port}")
        except Exception as exc:  # noqa: BLE001
            warn(f"could not stop PID {pid}: {exc}")


def launch(cmd: list[str], cwd: Path, log: Path, env: dict | None = None) -> None:
    LOGS.mkdir(exist_ok=True)
    logf = open(log, "ab")  # noqa: SIM115 - handed to the detached child
    subprocess.Popen(
        cmd, cwd=str(cwd), stdout=logf, stderr=subprocess.STDOUT,
        env={**os.environ, **(env or {})},
        creationflags=DETACHED if IS_WIN else 0, close_fds=True,
    )


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #
def backend_python() -> str:
    local = BACKEND / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
    if local.exists():
        ok(f"backend venv: {local}")
        return str(local)
    if DURABLE_VENV_PY.exists():
        ok(f"backend venv: {DURABLE_VENV_PY}")
        return str(DURABLE_VENV_PY)
    warn("no venv found - creating backend/.venv and installing requirements (one-time)")
    subprocess.run([sys.executable, "-m", "venv", str(BACKEND / ".venv")], check=True)
    pip = BACKEND / (".venv/Scripts/pip.exe" if IS_WIN else ".venv/bin/pip")
    subprocess.run([str(pip), "install", "-r", str(BACKEND / "requirements.txt")], check=True)
    ok("created backend/.venv")
    return str(local)


def ensure_database(env: dict[str, str], use_docker: bool) -> None:
    host = env.get("POSTGRES_HOST", "localhost")
    port = int(env.get("POSTGRES_PORT", "5432"))
    if port_open(host, port):
        ok(f"database already up on {host}:{port}")
        return
    if use_docker or (shutil.which("docker") and not PG_EXE.exists()):
        if not shutil.which("docker"):
            die("--docker requested but Docker is not installed.")
        say("starting database via docker compose")
        subprocess.run(
            ["docker", "compose", "up", "-d", "postgres", "redis", "chromadb"],
            cwd=str(ROOT), check=True,
        )
    elif PG_CTL.exists() and PGDATA.exists():
        say("starting bundled PostgreSQL")
        LOGS.mkdir(exist_ok=True)
        # Start via pg_ctl, NOT a raw detached postgres.exe. On Windows, launching
        # postgres.exe directly lets stray console Ctrl+C/close events reach its
        # worker processes and kill them with exit 0xC000013A (STATUS_CONTROL_C_EXIT),
        # so the server tears itself down seconds after starting. pg_ctl detaches the
        # server correctly (immune to those signals); `-w` blocks until it is genuinely
        # ready to accept connections, and it is the matching pair to `pg_ctl stop`.
        #
        # CRITICAL (Windows): do NOT use capture_output/PIPE here. pg_ctl starts a
        # long-lived postgres that inherits the pipe's write end, so subprocess.run
        # would block forever waiting for EOF (a hang at this step). Send pg_ctl's own
        # output to a file; the server log already goes to postgres.log via -l.
        ctl_log = open(LOGS / "pg_ctl.log", "ab")  # noqa: SIM115
        try:
            r = subprocess.run(
                [str(PG_CTL), "start", "-w", "-t", "45",
                 "-D", str(PGDATA), "-l", str(LOGS / "postgres.log"),
                 "-o", f"-p {port}"],
                cwd=str(STACK), stdout=ctl_log, stderr=ctl_log,
            )
        finally:
            ctl_log.close()
        if r.returncode != 0:
            die("pg_ctl could not start PostgreSQL within 45s "
                "(see logs/pg_ctl.log and logs/postgres.log)")
        ok(f"database up on {host}:{port}")
        return
    else:
        die(
            "No database available. Either install Docker Desktop and re-run with "
            "`python start.py --docker`, or provide a PostgreSQL on "
            f"{host}:{port} and set POSTGRES_* in backend/.env."
        )
    if not wait_for_port(host, port, timeout=30):
        die(f"database did not come up on {host}:{port} within 30s (see logs/postgres.log)")
    if not wait_for_pg_ready(host, port, timeout=30):
        die(f"database opened {host}:{port} but is still starting up after 30s "
            "(see logs/postgres.log)")
    ok(f"database up on {host}:{port}")


def seed(py: str) -> None:
    say("creating schema + seeding demo data (idempotent)")
    r = subprocess.run(
        [py, "scripts/seed_demo.py"], cwd=str(BACKEND),
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
    )
    if r.returncode != 0:
        die("seed failed (see output above)")
    ok("schema + demo data ready")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def do_stop() -> None:
    print("Stopping Enterprise AI Helpdesk ...")
    kill_port(BACKEND_PORT)
    kill_port(FRONTEND_PORT)
    if PG_CTL.exists() and PGDATA.exists():
        subprocess.run([str(PG_CTL), "-D", str(PGDATA), "stop", "-m", "fast"], capture_output=True)
        ok("bundled PostgreSQL stopped")
    ok("done")


def do_start(args: argparse.Namespace) -> None:
    print("=" * 62)
    print(" Enterprise AI Helpdesk - starting")
    print("=" * 62)

    if not BACKEND.exists():
        die(f"backend/ not found under {ROOT} - run this from the project root.")
    if shutil.which("python") is None and shutil.which("py") is None:
        die("Python not found on PATH.")

    env = load_env(BACKEND / ".env")
    if not (BACKEND / ".env").exists():
        warn("backend/.env missing - copy .env.example to backend/.env and set GEMINI_API_KEY")

    py = backend_python()
    ensure_database(env, use_docker=args.docker)
    seed(py)
    if args.seed_only:
        ok("--seed-only: done")
        return

    # backend
    kill_port(BACKEND_PORT)
    say(f"starting backend on http://{BACKEND_HOST}:{BACKEND_PORT}")
    launch(
        [py, "-m", "uvicorn", "app.main:app", "--host", BACKEND_HOST, "--port", str(BACKEND_PORT)],
        cwd=BACKEND, log=LOGS / "backend.log", env={"PYTHONPATH": str(BACKEND)},
    )
    if wait_for_http(f"http://{BACKEND_HOST}:{BACKEND_PORT}/health", timeout=45):
        ok("backend healthy")
    else:
        warn("backend did not report healthy in 45s (see logs/backend.log)")

    # frontend
    if not args.no_frontend:
        if shutil.which("npm") is None:
            warn("npm not found - skipping frontend (install Node.js to enable the UI)")
        else:
            kill_port(FRONTEND_PORT)
            say(f"starting frontend on http://localhost:{FRONTEND_PORT}")
            npm_cmd = ["cmd", "/c", "npm"] if IS_WIN else ["npm"]
            launch(
                npm_cmd + ["run", "dev", "--", "--port", str(FRONTEND_PORT), "--strictPort", "--host"],
                cwd=FRONTEND, log=LOGS / "frontend.log",
            )
            if wait_for_port("127.0.0.1", FRONTEND_PORT, timeout=40):
                ok("frontend up")
            else:
                warn("frontend not up in 40s (see logs/frontend.log)")

    print("\n" + "=" * 62)
    print(" READY")
    print("=" * 62)
    print(f"  UI        : http://localhost:{FRONTEND_PORT}")
    print(f"  API       : http://{BACKEND_HOST}:{BACKEND_PORT}")
    print(f"  API docs  : http://{BACKEND_HOST}:{BACKEND_PORT}/docs")
    print(f"  Health    : http://{BACKEND_HOST}:{BACKEND_PORT}/health")
    print(f"\n  Login (password for all: {DEMO_PASSWORD}) at org 'acme':")
    for email, role in DEMO_USERS:
        print(f"    {email:<20} {role}")
    print(f"\n  Logs      : {LOGS}")
    print(f"  Verify    : python scripts/verify.py")
    print(f"  Stop      : python start.py --stop")


def main() -> None:
    ap = argparse.ArgumentParser(description="Start/stop the Enterprise AI Helpdesk stack.")
    ap.add_argument("--stop", action="store_true", help="stop everything and exit")
    ap.add_argument("--seed-only", action="store_true", help="only create schema + demo data")
    ap.add_argument("--no-frontend", action="store_true", help="do not start the Vite UI")
    ap.add_argument("--docker", action="store_true", help="start DB/redis/chroma via docker compose")
    args = ap.parse_args()
    if args.stop:
        do_stop()
    else:
        do_start(args)


if __name__ == "__main__":
    main()
