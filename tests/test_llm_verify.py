"""Verification of model candidates: offsets in code, agreement, adjudication."""

from pathlib import Path

from tests.llm_fakes import PRIMARY, SECOND, STRONG, FakeProviders, client, run

from kipu.anonymizer import Anonymizer
from kipu.llm.extract import Candidate, Item, Target, Unit
from kipu.llm.verify import Dispute, Span, Verifier, adjudicate, by_surface


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
    u = unit("Yeferson Mora y la vecina doña tota llamaron desde Carulla")
    v = verifier(anonymizer)
    by_model = {
        PRIMARY: [
            Candidate(0, "Yeferson Mora", "PERSON"),
            Candidate(0, "tota", "PERSON"),
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
