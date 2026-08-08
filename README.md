# EMT — Doc/Code Validation Platform

Electron + FastAPI desktop app: ingest legacy mainframe COBOL/JCL + design docs, build code & doc graphs, validate docs against source.

## Layout
- `backend/` — FastAPI, structural_graph (ANTLR COBOL + JCL), doc_graph
- `src/` — Electron renderer (React + ReactFlow)
- `emt_data/` — public CardDemo fixtures, generated docs, tickets, plans

## Setup
- Frontend: `npm install` (runs backend venv setup via postinstall)
- Backend venv: `backend/.venv` (Python 3.11, uv)

## Note
ANTLR parsers under `backend/domain/structural_graph/*/generated/` are committed; regenerate from `grammar/*.g4` with `backend/tools/antlr/antlr-4.13.2-complete.jar`.
