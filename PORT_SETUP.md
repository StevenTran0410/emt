# Porting CodeSpectra (EMT) to a new machine — setup guide

This is a **zipped copy** of the project. The two large, machine-specific folders were intentionally left OUT of the zip and must be **recreated locally** (never copy them between machines):
- `node_modules/` — recreated by `npm install`
- `backend/.venv/` — recreated automatically by `npm install` (postinstall)

Everything else IS in the zip, including **`.database/`** (the pre-computed database — the whole reason for the zip; a normal `git clone` does NOT include it because it is gitignored).

Goal: follow these steps until **`npm run dev`** launches the app and the data is visible. An agent can execute this end-to-end.

---

## 0. Prerequisites (install if missing)
- **Node.js 20 LTS** (Electron 31 needs Node 18/20). Check: `node -v`.
- **Python 3.12** (3.11 also fine). Check: `python --version`.
- **uv** — the Python package manager the backend setup uses. Check: `uv --version`. If missing: `pip install uv` (or the standalone installer from https://docs.astral.sh/uv/). **uv is required** — without it, `npm install` skips backend setup.
- OS: Windows (paths below assume Windows; on macOS/Linux use `backend/.venv/bin/python`).

## 1. Install JS deps + auto-set-up the backend
From the project root:
```
npm install
```
`postinstall` runs `scripts/setup-backend.js`, which (if `uv` is present):
1. creates `backend/.venv` via `uv venv --python 3.11`,
2. installs backend deps via `uv pip install -e ".[dev,analysis]"` (fastapi, uvicorn, aiosqlite, pydantic, antlr4-python3-runtime==4.13.2, tree-sitter family, numpy/networkx/scikit-learn, pytest…).

Heavy ML extras (`torch`/`transformers` under `reranker`/`embeddings`) are **not** installed and are **not needed** for this demo.

**Verify:**
```
node -e "require('fs').accessSync('backend/.venv')" && echo venv-ok
backend\.venv\Scripts\python.exe -c "import fastapi, aiosqlite, antlr4, pydantic; print('backend deps ok')"
```

## 2. Confirm the database is in place
The app in dev reads the DB from `<project-root>/.database/codespectra.db` (the Electron main process sets `CODESPECTRA_DATA_DIR = <cwd>/.database`). This folder is included in the zip.
**Verify** (should print the file and a non-trivial size, plus a `graphs` folder):
```
dir .database
```
Expected: `codespectra.db` (tens of MB) and a `graphs\` directory. If `.database\codespectra.db` is missing, the app will start but show no data — copy it back from the zip into `<project-root>/.database/`.

## 3. Start the app
```
npm run dev
```
This runs `electron-vite dev`, which builds the renderer + main and spawns the backend automatically:
`backend/.venv/Scripts/python.exe backend/main.py --port <free>` with `CODESPECTRA_DATA_DIR=<cwd>/.database`.

**Success check:** the desktop window opens; navigate to **Flow Integrity** → the report page shows the business-flow coverage (19 business flows, per-flow cards, coverage %), and **Open Graph** shows the flow blocks with verdict colors. If that data renders, the DB + backend + frontend are all wired correctly.

## 4. Typecheck / tests (optional sanity)
```
npm run typecheck
backend\.venv\Scripts\python.exe -m pytest -q          # from d:\...\backend
```

---

## Troubleshooting
- **`npm install` warned "uv not found"** → `pip install uv`, then re-run `npm install` (or just `node scripts/setup-backend.js`).
- **`uv venv --python 3.11` fails** (no 3.11 on the machine and uv can't fetch it) → set it up manually with the installed Python:
  ```
  cd backend
  uv venv --python 3.12
  uv pip install -e ".[dev,analysis]"
  ```
- **App opens but shows no data** → `.database\codespectra.db` is not at the project root (or is empty). Put the file from the zip into `<project-root>/.database/`.
- **Backend fails to start** → confirm `backend\.venv\Scripts\python.exe` exists (re-run step 1). Check the terminal for the `[PythonProcessManager] Starting Python backend:` line and any Python traceback.
- **Clicking "Run" (recompute) fails / hangs** → EXPECTED on this machine: the LLM provider (OpenRouter) is network-blocked here. **Do not click Run.** All results (business flows, mappings, verdicts, coverage) are already computed and stored in the shipped DB — the app displays them read-only without any LLM call, so recomputation is unnecessary.
- **Do NOT** copy an old `backend/.venv` or `node_modules` from another machine — both contain absolute paths / platform binaries and will break. Always recreate via `npm install`.

---

### One-shot (if prerequisites are already installed)
```
npm install
dir .database        # confirm codespectra.db is present
npm run dev
```
