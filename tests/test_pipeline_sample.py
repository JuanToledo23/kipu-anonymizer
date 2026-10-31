import json
from pathlib import Path

import pytest
from scripts.evaluate import Evaluator, load_output

from kipu.config import Settings
from kipu.ingest.source import DirSource
from kipu.pipeline import run

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


@pytest.mark.skipif(not (SAMPLE / "input").is_dir(), reason="sample data not downloaded")
def test_sample_run_passes_all_sample_tests(tmp_path: Path) -> None:
    settings = Settings(tmp_path, "secret", "", "", "", 9000)
    report = run(DirSource(SAMPLE / "input"), tmp_path / "out", settings)
    assert report.missing_ids == {"chats": 0, "docs": 0, "tables": 0}
    assert report.audit_hits == {}
    records, entities = load_output(tmp_path / "out")
    evaluator = Evaluator(records, entities)
    tests = json.loads((SAMPLE / "tests_sample.json").read_text(encoding="utf-8"))
    failures = [(t["id"], reason) for t in tests for ok, reason in [evaluator.run(t)] if not ok]
    assert failures == []
