# Security

## Reporting a vulnerability

Please report security issues privately — open a
[GitHub security advisory](../../security/advisories/new) rather than a public issue. I'll
acknowledge it as soon as I can and keep you posted on the fix.

## Before you deploy this anywhere shared

This repository ships deliberately insecure defaults so it runs with one command on a laptop.
Change all of the following first:

| Default | Why it matters | Do this |
|---|---|---|
| `SECRET_KEY=dev-insecure-local-secret-key-…` | Signs every JWT. Anyone with it can forge a token for any user. | Set a unique random value, 32+ chars. Setting `APP_ENV=production` makes the app refuse to start with the dev value. |
| Seeded password `ChangeMe123!` | Documented publicly, applies to all four demo users including `admin@`. | Change it, or don't run `seed_demo.py` outside local development. |
| `POSTGRES_PASSWORD=postgres` / `helpdesk` | Local convenience credentials. | Use real credentials from a secret manager. |
| `CORS_ORIGINS` includes localhost | Fine locally, wrong in production. | Set it to your actual origins. |
| Rate limiting fails open when Redis is absent | By design, so local runs don't need Redis. | Run Redis in any shared deployment. |

## Handling API keys

`backend/.env` is gitignored and must stay that way — it's the only place a real key belongs.
`.env.example` files contain placeholders only. If you ever commit a key, rotate it rather
than only rewriting history; assume anything pushed was captured.

## What's already in place

- Argon2 password hashing (`passlib`), never plaintext or reversible storage.
- JWT access + refresh tokens with issuer/audience validation and server-side session
  revocation.
- Role-based access control — 20 granular permissions checked by FastAPI dependencies, not
  by convention.
- Every table is scoped by `org_id`, and queries are filtered by the caller's organization,
  so one tenant cannot read another's data.
- All SQL goes through SQLAlchemy in the repository layer; there is no string-built SQL. The
  natural-language database API is restricted to a fixed set of operations — the model picks
  an operation, it never emits SQL.
- Prompt-injection screening on inbound messages, and a grounding check on outbound answers
  so ungrounded text isn't presented as fact.
- RFC 7807 error responses that don't leak internals.
