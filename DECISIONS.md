# Decisions

## PII considered

- **Direct identifiers**: full names and every variant (given + first surname, first name alone
  when the person takes part in the thread or is a uniquely named employee, surname after a title,
  UPPERCASE without accents, filename tokens like `manuela_castano`), handles (users.json `name`
  and every email local part, as whole tokens anywhere: `?ref=agomez&`, `@arojas`, agent ids
  like `fernanda.vargas`; unknown `@handle` mentions too), emails (also spelled
  "arroba"/"punto"), cédulas (with or without dots; by context word, as the reply to a cédula
  request, or because the value is known), phones (10 digits starting with 3, optional +57, any
  grouping), plates (`AAA123`, `AAA-123`, `AAA 123`, motorcycles `AAA12A`/`AAA-12A`), passports
  (by context), birth dates (ISO and dd/mm/yyyy, mm/dd/yyyy), addresses (Colombian street
  grammar), Slack mentions `<@UID>`.
- **Handle and plate surrogates**: a handle becomes the local part of the person's surrogate
  email (same rule: `given.surname` for dotted handles, initial + surname otherwise); a plate
  keeps its class (letters/digits per position) and separator and never equals a plate seen in
  the input. Both are reserved in SQLite. A handle shared by two persons goes to the one in the
  thread's context, else to the newest person in the registry.
- **Where**: chat text, author, attachment filenames, doc filename/title/body (front matter,
  HTML comments and `<meta>` kept as raw lines), every metadata value, every table cell.
- **Third parties** not in any master table: "Given Surname" sequences built from name lexicons
  become new persons; unknown identifiers are attributed to the chat author (client), the
  thread's client, the table row owner, or the name right before them, else to a new person.
- **Quasi-identifiers**: barrio, occupation and year of joining. In `clientes` the occupation is
  generalized to a sector and the barrio swapped for another barrio of the same city (fixed per
  person). In free text, for the persons in context: barrio → city, occupation → sector, and the
  joining year → "hace unos años" when it co-occurs with another component.

## Kept on purpose and why

- Organizations, brands, regulators, wallets, places and public figures from
  `src/kipu/detect/gazetteer_co.txt` (Bancolombia, Nequi, PSE, Superfinanciera, Carulla, Éxito,
  Plaza Botero, Shakira, cities…): they are not natural persons and the buyer needs them. A
  longer person match still wins ("Mariana Carulla" is a person; "Carulla" alone is the store),
  and a surname after a title ("señor Santander") is a person, not the department.
- Record keys (`CL-…`, `KP-…`, `TX-…`, `PQR-…`): they are what keeps tables joinable.
- City, `fecha_vinculacion`, `ingreso`, amounts, dates of business events: not PII alone.
- The `Kipi` bot keeps its name.

## How consistency is kept

- One person = one cluster of seeds joined by strong keys (cédula, email, phone, uid, CL/KP ids)
  with cannot-link on different cédulas or two rows of the same master. Chat users without keys
  join a master row by unique name; homonyms are split by Slack username = email local part,
  then by identifiers in their own messages.
- Mentions of homonyms are resolved by the nearest evidence (CL id, radicado, cédula, email),
  then thread context, then source (employee in `empleados` and docs, client in client tables).
- `person_id` and every surrogate live in `$DATA_DIR/registry.sqlite`; surrogates are seeded by
  HMAC(secret, person_id|field|original|counter) and reserved with UNIQUE constraints, and they
  never reproduce a real name or identifier value.
- `pseudonyms.name` is always the canonical short form (given names + first surname), in
  `entities.json` and in `/anonymize`. Other rendered forms are still written but not declared.

## Supuestos verificados con la muestra

- Chat `author` is given names + first surname ("Yesenia Ramírez", not "Yesenia Ramírez Zapata"),
  as in `expected_output`; the pseudonym written there is the surrogate's short form.
- Doc `title` is the humanized filename with "de" inserted before a month name
  (`memo_cierre_julio.md` → "Memo cierre de julio"); the sample only confirms this one rule.
- XML attributes on child elements become a column `<child>_<attr>` (`documento` + `tipo` →
  `documento_tipo`).
- PDF `metadata` keeps every non-empty PyMuPDF key, including `format`, `creator` and
  `creationDate`. No PDF in the corpus has XMP metadata.
- Table keys: `cliente_id`, `tx_id`, `radicado` (pqrs attribute) and `id` (empleados); all
  8,297 ids match `record_index.json`.
- An occupation in free text is generalized whenever it matches a person in context; the joining
  year only when it co-occurs with another component (as in the sample's indirect test).
- A bare first name is a person only if it belongs to a thread participant or to the single
  employee with that first name.
- Registry schema extensions beyond the original plan: `persons.name/attrs`,
  `surrogates.original_norm` (one surrogate per original value, e.g. two emails), a `counters`
  table, and identifier types `handle`, `radicado` and `external`.
- The sample and the real corpus share generated values (e.g. `mcastano@gmail.com` for two
  different people), so the sample runs with its own `DATA_DIR`.

## Pipeline cost

- Tokens: 0 (no model calls).
- Time (2026-10-31, laptop): full run from the live API 28–29 s end to end for 8,297 records
  (183 requests, ~25 s of it spent downloading at a safe 500 requests/min); processing alone
  about 4 s (offline replay from `data/raw/`). The sample runs in 0.1 s.
