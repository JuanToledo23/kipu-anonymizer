# CLAUDE.md

Kipu Pay pseudonymization service for the Frontier challenge "Vende los datos sin vender a la gente".
It regenerates an anonymized corpus (chats, docs, tables + `entities.json`) from the sources API and
serves it over HTTP (`api/participante.openapi.yaml`). Rules only today; an LLM layer comes next.

## Stack
Python 3.13, uv, FastAPI + uvicorn, httpx, pydantic v2, PyMuPDF, lxml, regex, orjson, sqlite3, pytest, ruff.

## Layout
- `src/kipu/ingest/` source clients (`HttpSource`, `DirSource`) and parsers → normalized records
- `src/kipu/directory/` persons from clientes/empleados/users, SQLite registry (`registry.py`)
- `src/kipu/identity/` union-find resolution with cannot-link
- `src/kipu/detect/` rule detectors → `Mention`s (`patterns.py`, `names.py`, `engine.py`, gazetteer)
- `src/kipu/pseudo/` deterministic surrogates (HMAC seed + reservation), `names_co.py`
- `src/kipu/replace/` form-preserving rendering, generalization (occupation, barrio)
- `src/kipu/audit/` `texto.py` (port of validar.mjs `norm`/`contiene`), leak auditor
- `src/kipu/anonymizer.py` detect → replace for records (shared by pipeline and API)
- `src/kipu/pipeline.py` CLI run; `src/kipu/api.py` FastAPI app under `/v1`
- `scripts/evaluate.py` local evaluator; `tests/` unit tests (+ `tests/js` parity harness)

## Commands
- install: `make install` (= `uv sync`)
- lint: `make lint`; tests: `make test` (run `make lint test` before every commit)
- sample run + eval: `make evaluate-sample`
- full run + eval (needs `.env`): `make evaluate-full`
- service: `./run.sh` (port `$PORT`, default 9000) → `curl localhost:9000/v1/health`
- docker: `make docker-build`, then `docker run -p 9000:9000 --env-file .env -v kipu-data:/data kipu-anonymizer`
- validator: `node validar.mjs <out_dir> --public <dir with record_index.json>`

## Config (`.env`, see `.env.example`)
`PSEUDONYM_SECRET` (required), `FUENTES_URL`, `FUENTES_TOKEN`, `DATA_DIR` (default `data`),
`ENTRYPOINT_TOKEN` (optional bearer auth), `PORT`.

## Conventions
- Conventional Commits in English, imperative: `type(scope): summary` (feat, fix, test, docs,
  refactor, chore, ci). One logical change per commit; one "why" line in the body when not obvious.
- Stage explicit paths, never `git add -A`. Never push. No AI attribution anywhere.
- Commit only at green checkpoints, after `make lint test`.
- Type hints everywhere, small functions, docstrings only for non-obvious "why", no dead or
  commented-out code, no prints in library code (use `logging`).
- Docs (README.md, DECISIONS.md, docs/failures.md) change in the same commit as the code they describe.

## Hard rules
- No PII libraries or services (Presidio, scrubadub, Comprehend PII, ...). Rules and our own code only.
- Replace, never rewrite: only detected spans change; the rest of each record stays byte-identical.
- The registry (`$DATA_DIR/registry.sqlite`) is never rebuilt: person ids and surrogates persist
  across runs so pseudonyms stay stable when sources grow.
- Never log or print tokens or secrets (`FUENTES_TOKEN`, `PSEUDONYM_SECRET`, `ENTRYPOINT_TOKEN`).
- Never modify `validar.mjs`, `api/`, `ENUNCIADO.md`.

When unsure, state the assumption in the summary and keep going.
