# Decisions

The three answers the brief asks for (what counted as PII, what was kept on purpose, what the
pipeline costs), followed by the identity rules, the Págalo migration and the assumptions
checked against the sample. A Spanish summary is at the end.

Guiding principle: **the models propose, the code decides, replaces and verifies.** Rules run
first; the LLM layer only adds spans that the code can find verbatim and check.

## 1. What counted as PII

- **Direct identifiers**:
  - Full names and every variant: given + first surname; the first name alone when the person
    takes part in the thread or is a uniquely named employee; a surname after a title;
    UPPERCASE without accents; filename tokens like `given_surname`.
  - Handles: the users.json `name` and every email local part, as whole tokens anywhere
    (`?ref=<handle>&`, `@<handle>`, agent ids like `given.surname`), plus unknown `@handle`
    mentions.
  - Emails, also when spelled out with "arroba"/"punto".
  - Cédulas, with or without dots: by a context word, as the reply to a cédula request, or
    because the value is known.
  - Phones: 10 digits starting with 3, optional +57, any grouping.
  - Plates: `AAA123`, `AAA-123`, `AAA 123`, and motorcycles `AAA12A`/`AAA-12A`.
  - Passports (by context), birth dates (ISO, dd/mm/yyyy, mm/dd/yyyy), addresses (Colombian
    street grammar), account numbers (LLM layer, shape-checked) and Slack mentions `<@UID>`.
- **Where**: chat text, author and attachment filenames; doc filename, title and body (front
  matter, HTML comments and `<meta>` kept as raw lines); every metadata value; every table cell.
- **Third parties** not in any master table: "Given Surname" sequences built from name lexicons
  (or proposed by the LLM layer and verified) become new persons. An unknown identifier goes to
  the nearest preceding name in the same sentence, then the chat author (client), the thread's
  client or the table row owner, else to a new person.
- **Quasi-identifiers**: barrio, occupation and year of joining. In `clientes`, the occupation is
  generalized to a sector, and the barrio is swapped for another barrio of the same city (fixed per
  person). In free text, for the persons in context: barrio → city, occupation → sector, and the
  joining year → "hace unos años" when it co-occurs with another component. Ages and other
  quasi-identifying phrases in prose are generalized by the LLM layer.
- **Employee cargo**: in `empleados`, the `cargo` cell gets exactly what free text writes for it,
  namely its family ("Analista de Fraude" → "Analista", CEO/CTO → "directivo"). `ciudad`,
  `ingreso` and `reporta_a` stay as org structure. With the full cargo, the row plus any record
  naming the employee's city and year singled them out, because the indirect test joins records
  to the row.
- **Surrogate shape**: every pseudonym belongs to the same category as the original. Names keep
  their gender; cédulas, phones and plates keep their format; an email stays an email whose
  local part follows the person's surrogate name. Surrogates keep the original's form (case,
  accents, separators, spelled emails, digit grouping) and never reproduce a real value seen
  anywhere in the input.

## 2. What was kept on purpose, and why

- **Organizations, brands, regulators, wallets, places and public figures** from
  `src/kipu/detect/gazetteer_co.txt` (Bancolombia, Nequi, PSE, Superfinanciera, Carulla, Éxito,
  Plaza Botero, cities…). They are not natural persons, and the buyer needs them to train a
  support chatbot. A longer person match still wins: a person whose surname is "Carulla" is a
  person, and "Carulla" alone is the store. A surname after a title ("señor Santander") is a
  person, not the department.
- **Record keys** (`CL-…`, `PG-…`, `KP-…`, `TX-…`, `PQR-…`): they keep the tables joinable and
  identify no one outside the dataset.
- **City, `fecha_vinculacion`, `ingreso`, amounts and dates of business events**: none is PII on
  its own, and they carry the dataset's analytical value. They are generalized only when they
  co-occur as quasi-identifiers (above).
- **Product and campaign names** that look like given names (`Campaña "María"`): a PERSON hit
  right after "campaña", "promoción", "plan", "producto" or "tarjeta" is kept.
- **The `Kipi` bot** keeps its name.
- **Everything outside a detected span**: replace, never rewrite. The rest of each record is
  byte-identical to the source.

## 3. Pipeline cost

Measured on 2026-10-31 on the grown corpus (10,856 records: 2,999 chats, 34 docs, 7,823 table
rows), from the run's own usage log (`llm:` summary line) and on-disk LLM cache.

| Run | Input tokens | Output tokens | USD | Time |
|---|---:|---:|---:|---:|
| Full regeneration, LLM layer, cold cache | 3,447,937 | 1,219,585 | **2.00** | 546 s (~9 min) |
| Same corpus again, warm cache | 0 | 0 | 0.00 | ~19 s |
| Rules only (`LLM_ENABLED=false`) | 0 | 0 | 0.00 | 6.4 s processing, 9 s wall |

Per model, cold run:

| Role | Model | Calls | USD |
|---|---|---:|---:|
| Primary extractor | `anthropic/claude-haiku-5.5` | 546 | 0.63 |
| Second extractor | `google/gemini-3.5-flash-lite` | 1,086 | 1.26 |
| Adjudicator (disagreements, homonyms) | `anthropic/claude-sonnet-5.5` | 37 | 0.11 |

- Answers are cached by `sha256(model, prompt, schema, input)`, so a regeneration pays only for
  records that are new or changed. Growth of the sources costs in proportion to the growth.
- `LLM_MAX_COST_USD` (default 5) caps each run. Once it is reached the run makes no more calls and
  finishes with the rules, so a run never fails because of the LLM.
- Downloading from the sources API adds ~25–30 s (about 250 requests at a safe 500 requests/min).
- `/anonymize` runs the rules and the registry first, then, with the LLM layer on, lets the
  primary model add verified spans within a 20 s budget (`anonymize_budget`). Cached inputs make
  no new calls.

## 4. Identity rules

- **One person = one cluster of seeds joined by strong keys**: cédula, email, phone, Slack uid,
  and CL/PG/KP ids, combined with union-find. **Cannot-link** applies on different cédulas and on
  two rows of the same master table. A shared name never merges two persons, and neither does a
  shared email or phone alone.
- **A uid is a strong key.** An old user whose `real_name` was later shortened keeps their person,
  even when the short form now equals a new person's name.
- **No empty persons.** A cluster that cannot hold at least one identifier of its own is someone
  already known. It creates no person, and its records resolve through the owners of their
  identifiers.
- **Chat users without keys** join a master row by a unique name. Homonyms are split by Slack
  username = email local part, then by identifiers in their own messages.
- **Mentions of homonyms** are resolved by the nearest evidence, in this order:
  1. A CL id, radicado, cédula or email, or a code that a `transacciones` description ties to
     one client (for example a credit request `S-…`).
  2. Thread participants.
  3. Rare words shared with mentions of the same name that an identifier settles in another
     source.
  4. The owners of identifiers elsewhere in the thread, then persons named unambiguously there.
  5. With none of those, the strong model judges on evidence in the text (LLM runs, verified by
     code). Otherwise the source decides: an employee in `empleados` and docs, a client in client
     tables.

  A doc table column headed "Cliente" or "Agente" restricts the candidates to that kind. When the
  text says two mentions of a name are two persons ("son dos clientas distintas con el mismo
  nombre"), the mention nearest its evidence keeps the person and the other goes to the other
  homonym.
- **`empleados.reporta_a`** holds a name, not an id. It resolves to the one employee with that
  full or given + first surname name, who joins the row's context, so the cell carries the
  manager's pseudonym.
- **Persistence**: `person_id` and every surrogate live in `$DATA_DIR/registry.sqlite`, which is
  never rebuilt. Surrogates are seeded by HMAC(secret, person_id|field|original|counter) and
  reserved under UNIQUE constraints. The same person gets the same pseudonym in every run and in
  `/anonymize`.
- **Declared name**: `pseudonyms.name` is always the canonical short form (given names + first
  surname), both in `entities.json` and in `/anonymize`. Other rendered forms are written but not
  declared.

## 5. The Págalo migration (sources growth)

On 2026-10-31 the sources grew without notice from 8,297 to 10,856 records, because Kipu Pay
bought the Págalo portfolio. The growth was append-only: every old record was byte-identical.
It added 2 channels, 8 docs, 54 `PG-` clients, 6 employees (`KP-191`…`KP-196`), 48 Slack users,
10 PQRs and 1,510 transactions. `scripts/watch_sources.py` polls `record_index` and flagged the
change.

- **Re-listed clients are one person.** 12 `PG-` rows share the cédula and an email or phone with
  an existing `CL-` client (the migration minute itself says 12 accounts were unified). Two client
  rows sharing the cédula plus an email or phone merge, and the oldest row's profile (name,
  barrio) wins, so both rows render the same surrogates. A name-only or contact-only match still
  never merges. Before this rule, 7 of the 12 got a second name pseudonym (failure #16).
- **`PG-` ids are record keys**, exactly like `CL-`: evidence for homonyms, never an account
  number (failure #18).
- **Renamed old users keep their person.** Three old users' `real_name` lost its second surname,
  and each short form equals a new person's name. The uid decides, not the name.
- **No ghost persons.** Reruns on the same snapshot create nobody and are byte-identical
  (failure #17).
- **Outcome**: all 254 entities of the first official run kept their pseudonyms, and the full
  regeneration with the LLM layer took ~9 minutes.

## 6. Assumptions checked against the sample

- Chat `author` is given names + first surname (not the full three-part name), as in
  `expected_output`. The pseudonym written there is the surrogate's short form.
- A doc's `title` is the humanized filename with "de" inserted before a month name
  (`memo_cierre_julio.md` → "Memo cierre de julio"). The sample only confirms this one rule.
- XML attributes on child elements become a column `<child>_<attr>` (`documento` + `tipo` →
  `documento_tipo`).
- PDF `metadata` keeps every non-empty PyMuPDF key, including `format`, `creator` and
  `creationDate`. No PDF in the corpus has XMP metadata.
- Table keys: `cliente_id`, `tx_id`, `radicado` (pqrs attribute) and `id` (empleados). All ids
  match `record_index.json`.
- An occupation in free text is generalized whenever it matches a person in context. The joining
  year is generalized only when it co-occurs with another component (as in the sample's indirect
  test).
- A bare first name is a person only if it belongs to a thread participant or to the single
  employee with that first name.
- The sample and the real corpus share generated values (the same email for two different
  people), so the sample runs with its own `DATA_DIR` and registry.

## Resumen en español

- **Qué se consideró dato personal**: nombres en todas sus formas (completos, nombre + primer
  apellido, solo nombre en contexto, mayúsculas sin tildes, en nombres de archivo), handles de
  Slack y partes locales de correos, correos (también deletreados), cédulas, celulares, placas,
  pasaportes, fechas de nacimiento, direcciones, cuentas y menciones `<@UID>`, en texto, metadata,
  nombres de archivo y cada celda. Los cuasi-identificadores (barrio, ocupación, año de
  vinculación, cargo) se generalizan cuando aparecen juntos.
- **Qué se dejó a propósito**: empresas, marcas, reguladores, lugares y figuras públicas;
  llaves de registro (`CL-`, `PG-`, `KP-`, `TX-`, `PQR-`); ciudades, montos y fechas de negocio;
  nombres de campañas y productos; el bot Kipi; y todo el texto fuera de lo detectado.
- **Costo**: una regeneración completa con IA sobre 10.856 registros consumió 3,45 M tokens de
  entrada y 1,22 M de salida, US$2,00 y unos 9 minutos. Con caché caliente cuesta US$0 y unos 19 s;
  solo reglas, 6,4 s. El tope por corrida es `LLM_MAX_COST_USD` (5 por defecto).
- **Identidad**: se une por identificadores fuertes (cédula, correo, celular, uid, ids de cliente
  y empleado), nunca por el nombre. Los homónimos se separan por la evidencia más cercana, y el
  registro persistente mantiene cada seudónimo entre corridas.
- **Migración Págalo**: 12 clientes re-listados como `PG-` son la misma persona (cédula + correo o
  celular), los ids `PG-` son llaves de registro, los usuarios renombrados conservan su persona
  por uid, y ningún seudónimo existente cambió.
