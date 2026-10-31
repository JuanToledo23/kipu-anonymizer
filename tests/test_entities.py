from kipu.entities import _drop_shared_originals


def test_forms_of_two_entities_are_not_declared() -> None:
    entities = [
        {"entity_id": "p_1", "originals": ["Luis Santander", "Luis Santander Moreno", "a@x.co"]},
        {"entity_id": "p_2", "originals": ["LUIS SANTANDER", "Luis Santander Ruiz"]},
        {"entity_id": "p_3", "originals": ["Luis Santander"]},
    ]
    assert _drop_shared_originals(entities) == entities
    assert entities[0]["originals"] == ["Luis Santander Moreno", "a@x.co"]
    assert entities[1]["originals"] == ["Luis Santander Ruiz"]
    assert entities[2]["originals"] == ["Luis Santander"]  # never left without originals


def test_a_newer_entity_known_only_by_a_taken_form_is_not_declared() -> None:
    entities = [
        {"entity_id": "p_263", "originals": ["María"]},
        {"entity_id": "p_268", "originals": ["María"]},
    ]
    assert [e["entity_id"] for e in _drop_shared_originals(entities)] == ["p_263"]
