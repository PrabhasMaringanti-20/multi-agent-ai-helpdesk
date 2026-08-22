# Contributing

Thanks for taking a look. Issues and pull requests are both welcome — including small ones.
If you're planning something substantial, open an issue first so we don't duplicate work.

## Getting set up

```bash
git clone <your fork>
cd multi-agent-ai-helpdesk
cp .env.example backend/.env
python start.py
```

You don't need a model API key to develop: set `LLM_PROVIDER=fake` and
`EMBEDDING_PROVIDER=fake` in `backend/.env` and everything runs deterministically offline.

## Before you open a PR

```bash
# backend
cd backend
pytest -q
ruff check .
mypy app

# frontend
cd frontend
npm run build          # this also type-checks
```

All four should be clean. CI runs the same commands.

## House style

- **Respect the layering.** Only `app/repositories/` issues SQL. Routers call services;
  services call repositories. If you find yourself importing a model into a router, something
  has gone sideways.
- **Keep provider SDKs lazy.** Import them inside the adapter method, not at module top level,
  so an unused provider never becomes a hard dependency.
- **Config is data.** Categories, prompts, thresholds, and tool bindings live in
  `app/registries/`, not scattered through the code.
- **Match the surrounding code.** Same comment density, same naming, same idiom. Comments
  should explain *why*, not restate the line.
- Python targets 3.12, is formatted by `ruff`, and is fully type-annotated.
- Frontend is TypeScript with no `any` unless there's a comment explaining it.

## Adding an agent node

1. Write `app/agents/nodes/<name>.py` exporting a single `async def <name>(state, config)`.
2. Register it in `app/agents/nodes/__init__.py`.
3. Add the node and its edges in `app/agents/graph.py`; put routing logic in
   `app/agents/routing.py` (selectors read state, they don't mutate it).
4. Add a test in `backend/tests/unit/test_ai_engine.py`. There's a graph-reachability test
   that will fail if a node can't be reached from `START` or can't reach `END` — that's
   intentional, not an obstacle.

## Reporting a bug

Please include what you expected, what happened, and the smallest steps to reproduce it.
Logs from `logs/backend.log` help — just check them for keys before pasting.

## Security

Please don't file public issues for security problems. See [SECURITY.md](SECURITY.md).
