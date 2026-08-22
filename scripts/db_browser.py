#!/usr/bin/env python3
"""Terminal PostgreSQL browser for the Enterprise AI Helpdesk (no GUI).

    python scripts/db_browser.py                 # interactive menu
    python scripts/db_browser.py --tables        # list tables + row counts
    python scripts/db_browser.py --show users     [--limit 20]
    python scripts/db_browser.py --search tickets vpn   [--limit 20]

Reads connection settings from backend/.env (POSTGRES_*). Pure standard library
plus asyncpg (already a backend dependency). No PowerShell required.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    import asyncpg
except ImportError:  # pragma: no cover
    print("asyncpg is required. Activate the backend venv or `pip install asyncpg`.")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "backend" / ".env"

# Columns worth searching per well-known table (falls back to a text scan).
SEARCH_HINTS = {
    "users": ["email", "full_name"],
    "tickets": ["subject", "category", "status", "escalation_reason"],
    "conversations": ["title", "category", "status"],
    "kb_documents": ["title", "category", "doc_status"],
    "kb_chunks": ["text", "category_key"],
    "notifications": ["type", "status"],
    "messages": ["content", "role"],
}


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    return env


async def connect() -> "asyncpg.Connection":
    e = load_env(ENV_FILE)
    return await asyncpg.connect(
        user=e.get("POSTGRES_USER", "postgres"),
        password=e.get("POSTGRES_PASSWORD", "postgres"),
        host=e.get("POSTGRES_HOST", "localhost"),
        port=int(e.get("POSTGRES_PORT", "5432")),
        database=e.get("POSTGRES_DB", "helpdesk"),
    )


async def _tables(conn) -> list[str]:
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    return [r["tablename"] for r in rows]


async def list_tables(conn) -> None:
    print(f"\n{'TABLE':<32}{'ROWS':>10}")
    print("-" * 42)
    total = 0
    for t in await _tables(conn):
        try:
            n = await conn.fetchval(f'SELECT count(*) FROM "{t}"')
        except Exception:  # noqa: BLE001
            n = -1
        total += max(n, 0)
        print(f"{t:<32}{n:>10}")
    print("-" * 42)
    print(f"{'(rows total)':<32}{total:>10}\n")


def _pp(rows: list, limit: int) -> None:
    if not rows:
        print("  (no rows)")
        return
    cols = list(rows[0].keys())
    for i, r in enumerate(rows[:limit], 1):
        print(f"\n#{i}")
        for c in cols:
            val = r[c]
            s = "" if val is None else str(val).replace("\n", " ")
            if len(s) > 100:
                s = s[:100] + "…"
            print(f"  {c:<22}: {s}")
    print(f"\n  ({min(len(rows), limit)} of {len(rows)} shown)\n")


async def show(conn, table: str, limit: int) -> None:
    tables = await _tables(conn)
    if table not in tables:
        print(f"unknown table '{table}'. Available: {', '.join(tables)}")
        return
    order = "created_at" if await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name='created_at'", table
    ) else None
    q = f'SELECT * FROM "{table}"' + (f' ORDER BY {order} DESC' if order else "") + f" LIMIT {limit}"
    _pp(await conn.fetch(q), limit)


async def search(conn, table: str, term: str, limit: int) -> None:
    tables = await _tables(conn)
    if table not in tables:
        print(f"unknown table '{table}'. Available: {', '.join(tables)}")
        return
    cols = SEARCH_HINTS.get(table)
    if not cols:  # fall back to every text/varchar column
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=$1 AND data_type IN ('text','character varying')", table
        )
        cols = [r["column_name"] for r in rows]
    if not cols:
        print(f"no searchable text columns on '{table}'.")
        return
    where = " OR ".join(f'"{c}"::text ILIKE $1' for c in cols)
    q = f'SELECT * FROM "{table}" WHERE {where} LIMIT {limit}'
    _pp(await conn.fetch(q, f"%{term}%"), limit)


async def interactive(conn) -> None:
    await list_tables(conn)
    print("Commands:  tables | show <table> | search <table> <term> | quit")
    while True:
        try:
            raw = input("db> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            continue
        parts = raw.split()
        cmd = parts[0].lower()
        if cmd in ("quit", "exit", "q"):
            break
        elif cmd == "tables":
            await list_tables(conn)
        elif cmd == "show" and len(parts) >= 2:
            await show(conn, parts[1], 20)
        elif cmd == "search" and len(parts) >= 3:
            await search(conn, parts[1], " ".join(parts[2:]), 20)
        else:
            print("usage: tables | show <table> | search <table> <term> | quit")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Terminal PostgreSQL browser (no GUI).")
    ap.add_argument("--tables", action="store_true", help="list tables + row counts")
    ap.add_argument("--show", metavar="TABLE", help="print rows of a table")
    ap.add_argument("--search", nargs=2, metavar=("TABLE", "TERM"), help="search a table")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    try:
        conn = await connect()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect to PostgreSQL: {exc}\nIs the database running? (python start.py)")
        sys.exit(1)
    try:
        if args.tables:
            await list_tables(conn)
        elif args.show:
            await show(conn, args.show, args.limit)
        elif args.search:
            await search(conn, args.search[0], args.search[1], args.limit)
        else:
            await interactive(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
