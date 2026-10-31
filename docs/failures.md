# Failures found by the evaluator

Each entry: what the evaluator (validator, local tests or leak sweep) showed, why, what changed,
and the test that now covers it.

## 1. Surrogate equal to another person's real value

- **Symptom**: `validar.mjs` on the real data reported `la forma original '1973-07-16' sigue en
  la salida` for three `clientes` rows whose own birth dates had been replaced.
- **Root cause**: a generated birth date can coincide with the real birth date of a different
  person (only ~14,000 plausible dates), and the judge sweeps every real form in the whole output.
- **Fix**: `SurrogateFactory` takes an `is_real(field, value)` check backed by every identifier
  in the directory (including ones discovered during the run) and rejects such candidates.
- **Test**: `tests/test_surrogates.py::test_real_values_are_never_reused`.

## 2. Names split by a line break

- **Symptom**: the validator reported `'Jesús Sánchez' sigue en la salida` in `doc_04`.
- **Root cause**: PyMuPDF breaks lines inside names ("Jesús\nSánchez"); the name matcher only
  allowed spaces, `_`, `.` and `-` between tokens, while the judge's `contiene` tolerates any
  whitespace.
- **Fix**: the token gap also accepts a single line break, and name splitting uses `\s`.
- **Test**: `tests/test_names.py::test_names_split_by_a_line_break`.

## 3. "Luis Santander" resolved to the wrong homonym

- **Symptom**: `reporta_a` of the employees reporting to the fraud coordinator showed a pseudonym
  that matched no employee, breaking the "reports to" chain.
- **Root cause**: "Luis Santander" names both an employee (KP-005) and a client (CL-21823); with
  no context, the first candidate (the client) was chosen.
- **Fix**: ties go to the source's natural population (employees in `empleados` and docs,
  clients in client tables), after nearby evidence (CL/KP ids, PQR radicados, identifiers) and
  thread context.
- **Test**: `tests/test_render.py::test_reports_to_points_at_the_employee_homonym`.

## 4. Registry reuse merged two people who shared an email

- **Symptom**: after the sample and the real run shared a registry, `/anonymize` gave Manuela
  Castaño a brand-new pseudonym; her registry row had been renamed "Martina Castaño Martínez".
- **Root cause**: the real cluster for Martina found Manuela's registry id through the shared
  generated email `mcastano@gmail.com` and reused it, although their cédulas differ; lookups also
  returned owners from outside the run, which the renderer could not see.
- **Fix**: registry reuse applies cannot-link (different cédula, cliente_id, KP id or uid → no
  reuse), and `Directory.owner_of` only returns persons of the current run.
- **Test**: `tests/test_identity.py::test_shared_email_across_corpora_does_not_merge_persons`.

## 5. Homonym candidates lost before context could decide

- **Symptom**: live `/anonymize` mapped "Camila Arango (CL-12538)" and "Camila Arango
  (CL-57176)" to the same pseudonym.
- **Root cause**: the name index emitted one mention per variant kind ("full" for a user whose
  whole name is "Camila Arango", "short" for the two clients); overlap resolution kept only the
  "full" mention, so the clients were no longer candidates when the CL id was checked.
- **Fix**: one mention per span carrying every strong candidate; the nearest evidence picks.
- **Test**: `tests/test_names.py::test_one_mention_keeps_every_homonym_candidate` and
  `tests/test_render.py::test_homonyms_split_by_nearby_client_id`.

## 6. Client handles leaked inside URLs and @mentions

- **Symptom**: the pre-flight sweep of the deployed output found the users.json handles of three
  clients: `?ref=agomez&canal=chat`, `kit?u=khenao&lang=en` (chats) and `publica como @arojas`
  (`doc_23`), next to a correctly replaced name.
- **Root cause**: handles were only indexed as names when they had two tokens (`maria.lopez`);
  single-token handles and email local parts were never matched, and `@` mentions of unknown
  handles were not a detector at all. A dotted agent id shared with a client homonym
  (`andres.gomez`) also resolved by name to the client instead of the employee who owns it.
- **Fix**: `HANDLE` is a first-class identifier. `HandleMatcher` matches every known handle
  (users.json `name`, email local parts) case-insensitively as a whole token delimited by
  non-alphanumerics, plus unknown `@handle` mentions; the surrogate is the local part of the
  surrogate email, reserved in SQLite, and declared as `pseudonyms.handle`. Surrogate emails
  never reuse a real handle as local part.
- **Test**: `tests/test_handles.py` (URLs, paths, filenames, `@` mentions, boundaries, unknown
  handles, shared handles) and `tests/test_surrogates.py::test_handle_surrogate_follows_the_email_rule`.

## 7. Plates with a separator were not detected

- **Symptom**: the sweep found `JXA-971` (chat `ea88fd5f…`) and `GTD-596`
  (`transacciones:TX-000552`, `TX-000685`) unchanged; `GTD596` without the dash was replaced.
- **Root cause**: the plate pattern only accepted `AAA123`/`AAA12A`; the generator for
  same-shape surrogates also turned separators into digits.
- **Fix**: plates accept a dash or space after the letters (currency codes and uppercase street
  abbreviations excluded); the value is normalized to the compact form, so `GTD596` and
  `GTD-596` share one surrogate, which is written back with the original separator. Plates seen
  anywhere in the input are reserved as real values before rendering.
- **Test**: `tests/test_patterns.py::test_plates_both_classes_with_and_without_separators`,
  `test_plate_shaped_non_plates` and `tests/test_surrogates.py::test_surrogate_plate_keeps_its_class`.
