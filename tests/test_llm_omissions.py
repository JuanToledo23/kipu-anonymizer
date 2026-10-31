from kipu.detect.mention import Mention
from kipu.llm.omissions import suspect_records


def test_suspects_are_visible_bigrams_starting_with_a_given_name() -> None:
    raw = {
        ("r1", "text"): "Hablé con Lorena Quintero ayer.",
        ("r2", "text"): "Hablé con Lorena Quintero ayer.",
        ("r3", "text"): "Pagué en Banco Popular.",
        ("r4", "text"): "Llamó Lorena y luego Pedro.",
    }
    final = {("r2", "text"): [Mention(10, 25, "Lorena Quintero", "NAME", "p_1")]}
    assert suspect_records(raw, final) == ["r1"]
    assert suspect_records({**raw, ("r0", "body"): "Ana María"}, final, limit=1) == ["r0"]
