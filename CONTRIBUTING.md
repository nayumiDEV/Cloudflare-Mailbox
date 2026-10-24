# Contributing

Thanks for helping improve OTP Inbox. By participating you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

```bash
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git
cd Cloudflare-Mailbox

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# edit .env — set ADMIN_PASSWORD, WORKER_INGEST_TOKEN, COOKIE_SECURE=0 for local HTTP

uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and sign in with the admin credentials from `.env`.

Optional demo data:

```bash
python seed_demo.py
```

## Tests

```bash
# Backend (SQLite throwaway DB — never touches ./data)
python -m pytest tests -q

# Optional: exercise the PostgreSQL SQL dialect via the in-process shim
OTP_TEST_BACKEND=pg python -m pytest tests -q

# Frontend unit tests (Node 18+)
cd tests/js && npm install && npm run test:unit
```

Playwright E2E (`npm run test:e2e`) expects a running local server; skip it unless
you are changing UI flows.

## Pull requests

1. Fork and create a branch from `main`
2. Keep changes focused — one concern per PR when possible
3. Add or update tests for behaviour changes
4. Run the backend suite (and JS unit tests if you touched `static/`)
5. Fill in the PR template

### Style notes

- Backend is a FastAPI app in `app.py` with helpers in `db.py` / `schema.py`
- UI is vanilla JS in `static/app.js` (no bundler) — keep the re-render +
  `data-scroll` / `data-focus-key` pattern intact
- Prefer clear names and short comments that explain *why*, not *what*
- Do not commit secrets, `.env`, databases, or Wrangler credentials

## Reporting bugs / ideas

Use the GitHub issue templates. For security issues, see [SECURITY.md](SECURITY.md).

## License

Contributions are accepted under the [MIT License](LICENSE).
