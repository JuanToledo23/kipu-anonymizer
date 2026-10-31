import json
from pathlib import Path

import httpx
from scripts.watch_sources import load_state, poll


def test_watcher_uses_etags_and_flags_growth(tmp_path: Path, capsys) -> None:
    index = {"chats": ["a", "b"], "docs": ["d"], "tables": ["t"]}
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        etag = f'"{sum(len(v) for v in index.values())}"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304)
        return httpx.Response(200, json=index, headers={"etag": etag})

    baseline = tmp_path / "record_index.json"
    baseline.write_text(json.dumps(index))
    state_path, flag = tmp_path / "state.json", tmp_path / "changed.flag"
    state = load_state(state_path, baseline)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert not poll(client, "http://s/v1", state, state_path, flag)
        assert not poll(client, "http://s/v1", state, state_path, flag)
        index["chats"].append("c")
        assert poll(client, "http://s/v1", state, state_path, flag)
    assert seen == [None, '"4"', '"4"']
    out = capsys.readouterr().out
    assert "unchanged (304)" in out and "SOURCES CHANGED" in out
    assert json.loads(flag.read_text())["after"]["chats"] == 3
    assert load_state(state_path, baseline)["counts"]["chats"] == 3
