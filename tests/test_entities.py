from kipu.entities import _drop_shared_originals


def test_forms_of_two_entities_are_not_declared() -> None:
    entities = [
        {"entity_id": "p_1", "originals": ["Luis Santander", "Luis Santander Moreno", "a@x.co"]},
        {"entity_id": "p_2", "originals": ["LUIS SANTANDER", "Luis Santander Ruiz"]},
        {"entity_id": "p_3", "originals": ["Luis Santander"]},
    ]
    _drop_shared_originals(entities)
    assert entities[0]["originals"] == ["Luis Santander Moreno", "a@x.co"]
    assert entities[1]["originals"] == ["Luis Santander Ruiz"]
    assert entities[2]["originals"] == ["Luis Santander"]  # never left without originals
