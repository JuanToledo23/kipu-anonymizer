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

## 8. `/anonymize` declared a different name form than `entities.json`

- **Symptom**: red-team cases B01, B05, B09, B12, B13 and B14 returned the right `entity_id` and
  surrogate, but `pseudonyms.name` was "Given Surname1 Surname2" live and "Given Surname1" in
  `entities.json`. A record copied from our own output also created a new person (the pseudonym
  author was read as an unknown name).
- **Root cause**: `declared_pseudonyms` declared the longest name form rendered in the records at
  hand, so the declared name depended on the batch. In the API, nothing knew which values were
  our own surrogates.
- **Fix**: `pseudonyms.name` is always the registry's short form (given names + first surname).
  In `entities.json`, the validator still requires the value to appear in the person's records,
  so a person written only by given name declares that instead. The API (`live=True`) skips
  surrogate names, emails, phones and cédulas from the registry that no real person owns: they
  stay unchanged and are not declared.
- **Test**: `tests/test_canonical_name.py`,
  `tests/test_participants.py::test_live_mode_leaves_our_own_surrogates_alone`.

## 9. Lowercase given names of thread participants

- **Symptom**: 25 chats kept the agent's first name in a lowercase thank-you ("mil gracias!! x",
  "gracias x").
- **Root cause**: a bare given name was only a candidate when capitalized.
- **Fix**: lowercase given names (first or second given name) become `given_lower` mentions,
  assigned only to a person in the group's context: thread authors and persons named by a strong
  form or identifier in the thread, doc or row. There is no single-employee fallback. Given names
  that are everyday words (`luz`, `rosa`, `celeste`, ...) count only after a vocative (`gracias`,
  `hola`, `listo`, `ok`, `dale`, ...) or at the end of the message. The surrogate keeps the
  lowercase.
- **Test**: `tests/test_participants.py::test_lowercase_given_name_of_a_thread_participant`,
  `test_everyday_word_names_only_as_vocative`.

## 10. Names written with Cyrillic/Greek look-alike letters

- **Symptom**: red-team case B02 ("Lаdy" with Cyrillic а/е/о) was neither detected nor linked,
  but the validator's `norm` folds the same letters, so it counted as a leak.
- **Root cause**: detection ran on the raw text.
- **Fix**: detection runs on a Latin view built from validar.mjs's `CONFUSABLES` table (every
  entry maps one character to one character, so offsets are unchanged). `fold` applies the same
  map. Replacements apply to the original offsets, so the whole look-alike span is replaced.
- **Test**: `tests/test_participants.py::test_cyrillic_lookalikes_are_detected`.

## 11. Slack display names used as sign-offs

- **Symptom**: chat `690a150c…` ended with the author's own users.json `display_name`, unchanged.
- **Root cause**: `display_name` was never a name form.
- **Fix**: a `display_name` of 3 or more characters that differs from the person's given names
  is stored in the person's attrs and indexed as a given-name form. Like any bare given name, it
  only counts for a person in context, and it renders as the surrogate's given name. Its
  tokens are forbidden in surrogates.
- **Test**: `tests/test_participants.py::test_display_name_signoff_is_replaced`.

## 12. Identifiers went to the chat author instead of the person named before them

- **Symptom**: on the deployed service, red-team cases B10 ("Soy <known full name>, CC <new
  number>") and B11 (two unknown homonyms with different cédulas in one batch) created no new
  person: the cédula went to the author of the copied chat. The local run passed only because
  the harness counted a stale registry person behind the author as "new". With a stricter
  harness, B08 and B11 also showed unknown names cut at the lexicon's edge: a given name missing
  from the lexicon ("soy Wendy ...") and an unlisted second surname stayed in the text.
- **Root cause**: an identifier was linked to a preceding name only within 4 characters
  (", CC " is 5), and before that the client author won. An identifier contradicting a
  person's cédula was attached anyway (unknown names) or dropped to an anonymous person. The
  external-name detector only covered lexicon tokens.
- **Fix**: a new identifier goes to the nearest strong or unknown full name before it in the
  same sentence, up to 40 characters away, before the author or row-owner fallbacks. If that
  person already has a different cédula (or another one-per-person id), the name belongs to a
  homonym: a new person keyed by name + identifier, and the name mention is re-owned so it gets
  its own pseudonym. A new identifier of a type the person lacks (a first phone, an email) is
  added to the same person. An unknown name also absorbs one following capitalized word
  (second surname) and, after "soy"/"me llamo"/"mi nombre es", one preceding one. Kept
  gazetteer terms ("Juan Valdez") are never widened. In the harness, "new person" is judged on
  the entity that carries the named person's name, never the author's.
- **Test**: `tests/test_linking.py` (contradicting cédula → new person, first phone → same
  person, two homonyms → two persons, 5–40 character gaps, widened unknown names, kept terms
  not widened).

## 13. Growth-intolerant ingestion

- **Symptom**: with a grown copy of the sources, a table or doc format outside the output
  schema crashed the whole run (`TableRecord.table` literal); a date or doc listed but answering
  404 failed the run; a 304 whose cached copy was gone produced an empty body; a connection
  error surfaced as a bare httpx error without the URL. `DirSource` read four hardcoded table
  files.
- **Root cause**: ingestion assumed the listings and the cache were always consistent.
- **Fix**: `DirSource` lists tables from the saved `_index.json` (or the files present) and
  CSVs take charset and separator from the listing. Missing dates, channels without a dates
  listing, missing docs and tables are skipped with a log line; tables or doc formats outside
  the output schema are skipped with a warning. A 304 without a cached copy refetches
  unconditionally. Failures raise `SourceError` naming the URL and the attempts.
- **Test**: `tests/test_ingest_growth.py`.

## 14. A run and /anonymize could race on the registry

- **Symptom**: found by review while adding concurrency tests: two connections (a run and an
  `/anonymize` batch) could both decide a person or a surrogate did not exist yet.
- **Root cause**: `external_person` and surrogate reservation were check-then-insert across
  separate statements, the write lock was per connection, and `BEGIN` was deferred (a
  read-then-write transaction can fail with `SQLITE_BUSY` instead of waiting). A lost
  reservation race made the factory try further candidates for a (person, field, original)
  that already had a value.
- **Fix**: one process-wide write lock for every registry connection, `BEGIN IMMEDIATE`, an
  explicit `busy_timeout`, and atomic get-or-create for external persons and surrogates
  (`reserve_surrogate` returns the value already stored when another connection won).
- **Test**: `tests/test_service.py::test_parallel_anonymize_batches_share_new_persons`,
  `test_anonymize_answers_during_a_run_and_the_run_keeps_its_persons`.

## 15. Jobs lost on restart; a failure before the pipeline left a job running

- **Symptom**: after a restart `GET /run/{id}` answered 404 for every earlier job. An error
  while building the source client (before the `try`) left the job `running` forever.
- **Root cause**: jobs lived only in memory, and the job body started outside the error handler.
- **Fix**: the job list persists in `$DATA_DIR/jobs.json` (atomic write on every change); jobs
  found `queued`/`running` at startup become `failed` ("interrupted"). The whole job body runs
  inside the handler, which records the error and the finish time.
- **Test**: `tests/test_service.py::test_output_entities_and_jobs_survive_a_restart`,
  `test_a_run_interrupted_by_a_restart_is_reported_failed`,
  `test_unexpected_pipeline_error_fails_the_job_and_keeps_the_output`.
