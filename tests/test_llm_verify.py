"""Verification of model candidates: offsets in code, agreement, adjudication."""

from pathlib import Path

from tests.llm_fakes import PRIMARY, SECOND, STRONG, FakeProviders, client, run

from kipu.anonymizer import Anonymizer
from kipu.llm.extract import Candidate, Item, Target, Unit
from kipu.llm.verify import Dispute, Span, Verifier, adjudicate, by_surface, valid_shape


def unit(*texts: str) -> Unit:
    return Unit("u", "chat", [Item(t, (Target(f"m{i}", "text"),)) for i, t in enumerate(texts)])


def verifier(anonymizer: Anonymizer, covered: dict | None = None) -> Verifier:
    return Verifier(
        anonymizer.index, anonymizer.kept, covered or {}, [PRIMARY, SECOND], ["primary", "second"]
    )


def test_offsets_are_computed_in_code(anonymizer: Anonymizer) -> None:
    u = unit("mi cuñado Yeferson vino; Yeferson pagó", "llamen a Yeferson")
    v = verifier(anonymizer, {("m1", "text"): [(9, 17)]})
    spans = v.spans(u, Candidate(0, "Yeferson", "PERSON"), "primary")
    assert [(s.record_id, s.start, s.end) for s in spans] == [("m0", 10, 18), ("m0", 25, 33)]
    assert v.spans(u, Candidate(0, "Yefersson", "PERSON"), "primary") == []


def test_lowercase_single_name_only_where_the_model_saw_it(anonymizer: Anonymizer) -> None:
    u = unit("la pipa y pipe llegaron; pipe pagó")
    v = verifier(anonymizer)
    c = Candidate(0, "pipe", "PERSON", left="pagó; ", right=" pagó")
    assert [(s.start, s.end) for s in v.spans(u, c, "primary")] == [(10, 14)]
    seen = Candidate(0, "pipe", "PERSON", left="llegaron; ", right=" pagó")
    assert [(s.start, s.end) for s in v.spans(u, seen, "primary")] == [(25, 29)]


def test_agreement_union_and_adjudication(anonymizer: Anonymizer, tmp_path: Path) -> None:
    u = unit("Yeferson Mora y la clienta doña tota llamaron desde Carulla")
    v = verifier(anonymizer)
    by_model = {
        PRIMARY: [
            Candidate(0, "Yeferson Mora", "PERSON"),
            Candidate(0, "tota", "PERSON", role="client"),
            Candidate(0, "Carulla", "PERSON"),
        ],
        SECOND: [Candidate(0, "Yeferson Mora", "PERSON"), Candidate(0, "Carulla", "ORG")],
    }
    accepted, disputed = v.classify(u, by_model)
    assert [(s.text, s.source) for s in accepted] == [("Yeferson Mora", "both")]
    assert disputed and {g.candidate.text for g in disputed} == {"tota"}  # Carulla: gazetteer

    fake = FakeProviders({STRONG: lambda user: {"verdicts": [{"id": 0, "personal": True}]}})
    strong = run(adjudicate(client(tmp_path, fake), STRONG, v, [Dispute(u, disputed)]))
    assert [(s.text, s.source) for s in strong] == [("tota", "strong")]

    alone = {SECOND: [Candidate(0, "Yeferson Mora", "PERSON")]}
    accepted, disputed = v.classify(u, alone)
    assert [(s.text, s.source) for s in accepted] == [("Yeferson Mora", "second")] and not disputed


def test_rule_spans_and_overlaps_win(anonymizer: Anonymizer) -> None:
    u = unit("Yeferson Mora Ruiz")
    v = verifier(anonymizer, {("m0", "text"): [(9, 13)]})
    assert v.spans(u, Candidate(0, "Yeferson Mora", "PERSON"), "x") == []
    spans = [
        Span("m0", "text", 0, 8, "Yeferson", "PERSON"),
        Span("m0", "text", 0, 13, "x", "PERSON"),
    ]
    assert [s.end for s in by_surface(spans)[("m0", "text")]] == [13]


def test_titles_are_not_persons(anonymizer: Anonymizer) -> None:
    v = verifier(anonymizer)
    accepted, disputed = v.classify(
        unit("Señor\nYeferson Mora"), {PRIMARY: [Candidate(0, "Señor", "PERSON")]}
    )
    assert accepted == [] and disputed == []


def test_record_ids_are_never_accounts() -> None:
    for code in ("PG-3773", "CL-15468", "TX-006080", "Cliente PG-3773"):
        assert not valid_shape(Candidate(0, code, "ACCOUNT"))
    assert valid_shape(Candidate(0, "4567-1234-9988", "ACCOUNT"))


def test_common_words_are_not_persons(anonymizer: Anonymizer) -> None:
    v = verifier(anonymizer)
    u = unit(
        "Hola. Compré un televisor",
        "Crédito vehicular placa GTD596. Esposo autorizado",
        "Saludos, Equipo Kipu",
    )
    by_model = {
        PRIMARY: [
            Candidate(0, "Compré", "PERSON"),
            Candidate(1, "Esposo", "PERSON", "m", "third_party"),
            Candidate(2, "Equipo", "PERSON", role="employee"),
        ],
        SECOND: [Candidate(0, "Compré", "PERSON", role="client")],
    }
    assert v.classify(u, by_model) == ([], [])


def test_lone_token_needs_lexicon_or_named_role(anonymizer: Anonymizer) -> None:
    v = verifier(anonymizer)
    u = unit("lo atendió Zorbaq y luego Zorbaq volvió")
    third = {PRIMARY: [Candidate(0, "Zorbaq", "PERSON", role="third_party")]}
    assert v.classify(u, third) == ([], [])
    client = {PRIMARY: [Candidate(0, "Zorbaq", "PERSON", role="client")]}
    assert [s.text for s in v.classify(u, client)[0]] == ["Zorbaq", "Zorbaq"]


def test_campaign_names_are_products(anonymizer: Anonymizer) -> None:
    v = verifier(anonymizer)
    u = unit(
        'Campaña "María" (octubre); la campaña María cierra',
        "la campaña de octubre se va a llamar 'María'. Habló María",
        "que salga una tarjeta edición María con orquídeas",
    )
    by_model = {
        PRIMARY: [
            Candidate(0, "María", "PERSON"),
            Candidate(1, "María", "PERSON"),
            Candidate(2, "María", "PERSON"),
        ],
        SECOND: [Candidate(0, "María", "PERSON")],
    }
    accepted, _ = v.classify(u, by_model)
    second = u.items[1].text
    assert [(s.record_id, second[s.start - 6 : s.end]) for s in accepted] == [("m1", "Habló María")]
