# Kipu Pay anonymizer

Pseudonymization service for the Frontier challenge "Vende los datos sin vender a la gente"
(brief in `ENUNCIADO.md`). It downloads Kipu Pay's support chats, internal documents and tables
from the sources API, replaces every personal datum with a consistent, same-category pseudonym,
generalizes quasi-identifiers, and serves the four `output/` files plus a live `/anonymize`
endpoint (`api/participante.openapi.yaml`). Rules only for now; no LLM calls.

## Run

```bash
cp .env.example .env          # fill FUENTES_URL, FUENTES_TOKEN and a random PSEUDONYM_SECRET
make install
./run.sh                      # http://localhost:9000/v1/health
```

Batch run without the service:

```bash
uv run python -m kipu.pipeline --source http --out data/out_full    # live API, DATA_DIR=data, raw in data/raw/
DATA_DIR=data/sample_run uv run python -m kipu.pipeline --source dir:data/sample/input \
  --out data/sample_run/output                                           # sample, own registry
```

Every route is served at both `/v1/...` and `/...`. With `ENTRYPOINT_TOKEN` set, all routes
require `Authorization: Bearer <token>` except `GET /health` (no secrets in it).

Real runs use `DATA_DIR=data`; the sample always uses `DATA_DIR=data/sample_run` so its persons
never enter the real registry.

Docker: `make docker-build` then
`docker run -p 9000:9000 --env-file .env -v kipu-data:/data kipu-anonymizer`.
Keep the `/data` volume: it holds the registry that keeps pseudonyms stable between runs.

Production runs on Fly.io (`https://kipu-anonymizer.fly.dev/v1`); see [docs/deploy.md](docs/deploy.md)
for deploys, secrets and logs. Never deploy while an evaluation run is in progress.

## Evaluate

```bash
make evaluate-sample   # sample run (DATA_DIR=data/sample_run), validar.mjs, the 13 sample tests and a field diff vs expected_output
make evaluate-full     # live run, validar.mjs and the 5 public tests
```

`scripts/evaluate.py <out> --public <dir> [--tests f.json ...] [--expected <dir>]` runs the
validator, every test by family (directa, oculta, falso_positivo, homonimos, consistencia,
indirecta) and exits 1 on any failure.

```bash
make preflight                                        # sweep data/out_full vs the newest data/raw/<run>
make preflight PREFLIGHT_OUT=data/preflight PREFLIGHT_RAW=data/raw/<run>
```

`scripts/preflight.py <out> --raw <data/raw/<run>> [--public <dir>]` is the hostile sweep run
before requesting an evaluation. With the judge's `contiene` it looks for every master-table
value (clientes, empleados, cédula and agente of pqrs), every users.json name, handle, email and
phone, and every person or email in doc metadata; it lists emails, phones, plates and cédulas in
the output that are not declared surrogates, searches UPPERCASE-without-accents name forms, and
checks structure against `record_index.json` (ids once, columns, metadata keys, attachments). It
prints a table and exits 1 on any real hit. Categorical columns (`tipo_doc`, `barrio`,
`ocupacion`, `notas`) and plain amounts are not counted.

## Architecture

1. **Ingest**: `HttpSource`/`DirSource` → normalized chat, doc and table records.
2. **Directory + identity**: persons seeded from clientes, empleados and users, joined by strong keys (union-find, cannot-link), stored in a persistent SQLite registry.
3. **Detect**: regex identifiers + directory-driven name variants + gazetteer, resolved per thread/record with context (participants, row owner, nearby ids).
4. **Pseudonymize + replace**: HMAC-seeded surrogates reserved in SQLite, written back with the original form (case, accents, separators, spelled emails, digit grouping).
5. **Audit**: a Python port of the judge's `contiene` sweeps the output for every original form before publishing.

Design notes: [docs/index.html](docs/index.html). Decisions and costs: [DECISIONS.md](DECISIONS.md).
