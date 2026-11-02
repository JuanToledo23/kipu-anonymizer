# Kipu Anonymizer

**English** · [Español](README.es.md)

> Sell the data without selling the people: consistent, verifiable pseudonymization of
> support chats, documents and tables.

Kipu Pay, a Colombian fintech, wants to sell its customer-support corpus to train a chatbot. The
corpus is full of personal data: names, cédulas, phones, emails, plates, addresses, and details
that identify someone without naming them. This service regenerates the whole corpus with every
real person replaced by an invented, same-category identity that stays the same across records,
across runs and as the sources grow. Only the detected fragments change; every other byte of
each record is left as it was. It was built for the Frontier challenge
"Vende los datos sin vender a la gente".

## Results

| Official evaluation (hidden test set) | Score | Notes |
|---|---:|---|
| Run 1: rules only | **81 / 95** | 92.5 / 100, **1st of 7 teams** |
| Run 2: rules + LLM layer | **146 / 156** | **zero direct leaks** from this run on |
| Run 3: 100 % of the set | **197 / 205** | easy 94/94 · hard 40/43 |

| Cost per full regeneration (10,856 records) | |
|---|---|
| With the LLM layer, cold cache | **≈ US$2**, **~9 min** (3.4 M input + 1.2 M output tokens) |
| Rules only | **~6 s** |
| Rerun with warm cache | US$0, ~19 s |

When the sources grew by 2,559 records (an acquired company's portfolio), no existing pseudonym
changed.

## Architecture

Seven steps, each a module under `src/kipu/`. The design principle is that **the models propose,
and the code decides, replaces and verifies.**

1. **Ingestion** (`ingest/`). Downloads chats, documents (PDF, Markdown, HTML) and tables (CSV,
   XML) from the sources API. Every one is read in full, including metadata, HTML comments and
   file names, and converted to one record schema; a raw copy is kept on disk.
2. **Person directory** (`directory/`). Builds the list of who exists from the client and employee
   tables and from the Slack users, before any free text is read. Each person gets a fixed
   internal id in a SQLite registry.
3. **Detection** (`detect/`, `llm/`). Rules find everything with a format: cédulas, phones,
   emails, plates, addresses and every known name variant. Two LLMs then propose what the rules
   cannot see (third parties, nicknames, data in prose). A proposal counts only if its exact text
   exists in the record and passes shape checks.
4. **Identity** (`identity/`). Decides which person each mention belongs to, using strong
   identifiers (cédula, email, phone, client id, Slack uid) and union-find with cannot-link.
   A stronger model breaks only the ties that the evidence leaves open, and the code verifies its answer.
5. **Pseudonyms** (`pseudo/`). Generates a believable, same-gender, same-format identity for each
   person, seeded by HMAC from a secret and the person id. Every pseudonym is reserved in the
   registry, so it is unique and stable over time.
6. **Replacement and generalization** (`replace/`). Swaps each detected span in place, keeping
   its form (case, missing accents, separators, digit grouping). Information that identifies
   someone without naming them (barrio, occupation, joining year) is made less precise.
7. **Independent audit** (`audit/`). Searches the whole output for every original value with a
   port of the judge's own normalization (accents, look-alike letters, separators). Any hit sends
   that record back for reprocessing, and nothing is published until the audit is clean.

Design page (diagram and the seven modules, English/Spanish):
<https://juantoledo23.github.io/kipu-anonymizer/>. Pitch:
<https://juantoledo23.github.io/kipu-anonymizer/pitch.html>. Both are also in [`docs/`](docs/README.md).

## Key design decisions

- **Rules first, LLM on top.** Rules alone fall short on free text, and an LLM alone invents and
  over-redacts. The models only add what the rules missed, and the service still produces a full
  output without them.
- **The LLM returns exact strings, never rewritten text.** The code locates each string, checks
  its shape and replaces it in place, so the rest of every record is byte-identical to the source.
- **Persistent registry and deterministic pseudonyms.** Person ids and pseudonyms live in SQLite
  and are never rebuilt. Random pseudonyms per run would have broken the dataset when the sources
  grew.
- **Independent auditor with reprocessing.** A separate pass sweeps the output for every known
  original value, the same way the judge does. Leaks are reprocessed, never just reported.
- **Identity by strong identifiers, never by name.** Two people with the same name stay two
  people, and one person listed twice (same cédula and contact) is one.

Details, costs and trade-offs: [DECISIONS.md](DECISIONS.md). Every defect found along the way:
[docs/failures.md](docs/failures.md).

## Quickstart

Prerequisites: Python 3.13, [uv](https://docs.astral.sh/uv/), and Node ≥ 20 (for the official
validator `validar.mjs`).

```bash
cp .env.example .env     # set PSEUDONYM_SECRET, FUENTES_URL, FUENTES_TOKEN; API keys for the LLM layer
make install             # uv sync
make run                 # service on http://localhost:9000 → curl localhost:9000/v1/health
make lint test           # ruff + pytest
```

Evaluation:

```bash
make evaluate-sample     # sample corpus (own DATA_DIR and registry) + validator + sample tests + diff vs expected
make evaluate-full       # live sources API + validator + public tests
make preflight           # hostile leak sweep of data/out_full against the newest raw snapshot
```

`LLM_ENABLED=false` runs rules only. `LLM_MAX_COST_USD` (default 5) caps each run, and a run that
hits the cap finishes with the rules. Every variable is documented in [`.env.example`](.env.example).

**Deploy.** It runs as a Docker image (`make docker-build`) with a persistent volume at `/data`
for the registry and the last output. Production runs on Fly.io as a single always-on machine;
[docs/deploy.md](docs/deploy.md) covers secrets, redeploys, logs and the rule never to deploy
during an evaluation run.

## API

Served under `/v1` (and at the root). With `ENTRYPOINT_TOKEN` set, every route except `/health`
requires `Authorization: Bearer <token>`. The contract is
[`api/participante.openapi.yaml`](api/participante.openapi.yaml).

| Method | Path | What it does |
|---|---|---|
| `GET` | `/health` | Liveness, team, version and the configured models. |
| `POST` | `/run` | Starts a full regeneration from the sources API and returns a `job_id` (202). |
| `GET` | `/run/{job_id}` | Job status, record counts, audit result, tokens and cost. |
| `GET` | `/output/{file}` | One of the four files of the last completed run (`chats.jsonl`, `docs.jsonl`, `tables.jsonl`, `entities.json`). |
| `GET` | `/entities` | The full pseudonym dictionary (`entities.json`), for the judge only. |
| `POST` | `/anonymize` | Anonymizes new records live, with the same pseudonyms as the run. |

A failed run never replaces the last good output, and jobs survive restarts.

## Repository layout

```
src/kipu/
  ingest/      sources API client, local replay, parsers for PDF/MD/HTML/CSV/XML
  directory/   persons from master tables and Slack users; SQLite registry
  identity/    union-find with cannot-link; homonym resolution
  detect/      regex detectors, name index, gazetteer of kept terms, quasi-identifiers
  llm/         model client and cache, extraction, verification, omissions pass, generalization
  pseudo/      HMAC-seeded surrogates and Colombian name lists
  replace/     form-preserving rendering and generalization (occupation, barrio)
  audit/       port of the validator's normalization; leak auditor
  anonymizer.py, pipeline.py, api.py, entities.py, config.py
scripts/       evaluator, preflight sweep, identity and quasi-identifier checkers, source watcher
tests/         unit and service tests; redteam/ adversarial cases; js/ parity harness
docs/          design page, pitch, deploy guide, failure log
api/           OpenAPI contracts (provided by the challenge)
validar.mjs    official output validator (provided by the challenge)
ENUNCIADO.md   challenge brief (Spanish, provided by the challenge)
```

## Evaluation and verification

| Tool | What it checks |
|---|---|
| `scripts/evaluate.py` | Runs `validar.mjs`, then every local test by family (direct, hidden, false positive, homonyms, consistency, indirect), and optionally a field diff against an expected output. Exits 1 on any failure. |
| `scripts/preflight.py` | Hostile leak sweep with the judge's matching: every master-table value, every Slack name, handle, email and phone, person names in doc metadata, undeclared emails/phones/cédulas/plates, UPPERCASE-without-accents forms. Also checks structure against `record_index.json`. |
| `tests/redteam/sweep_output.py` | Red-team sweep A1–A7 of an output against its raw snapshot; `run_anonymize_cases.py` sends adversarial `/anonymize` cases (lowercase, spelled emails, Cyrillic look-alikes, homonyms) to a live service. |
| `scripts/quasi_check.py` | For each person with a master row, finds records where barrio, occupation and year (cargo, city and year for employees) still co-occur. Fails on any record with all three. |
| `scripts/identity_audit.py` | Same-name persons, unlinked chat users, records an entity misses, forms declared by two entities, and pseudonyms changed since a baseline. Runs on a copy of the registry. |
| `scripts/judge_identity.py` | Replays the judge's consistency and homonym families over every person in the registry, not just the few the public tests name. |

`make lint test` runs 155 automated tests, including a parity harness that checks the Python
normalization against the validator's JavaScript.

## Limits and non-goals

- **Ambiguous bare mentions.** A lone given name or surname with no participant, identifier or
  context to anchor it may be left as is or assigned to the wrong homonym. The rules prefer
  leaving a common word alone over replacing it.
- **No manual recall annotation.** Recall is measured against every value the master tables
  and Slack know, against the challenge's tests and against adversarial cases, not against a
  hand-labelled corpus. A third party who appears only in prose and is missed by both models
  and the omission pass would not be counted.
- **Pseudonymization, not differential privacy.** The output keeps real structure, dates, amounts
  and generalized quasi-identifiers, and gives no formal guarantee against linkage attacks. The
  registry is a re-identification key; it stays on the server and is never part of what is sold.
- **Third-party models.** With the LLM layer on, record text is sent to the model providers
  (through OpenRouter, with Anthropic as fallback). Run with `LLM_ENABLED=false` where that is
  not acceptable.
- **Legal basis is out of scope.** Consent, purpose limitation and compliance with Colombian data
  protection law (Ley 1581 de 2012) are the data owner's decisions, not this service's.

## License and author

[MIT](LICENSE) © 2026 Juan Toledo. Built for the Frontier challenge. The challenge material
(`ENUNCIADO.md`, `api/`, `validar.mjs`) belongs to its organizers.
