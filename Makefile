.PHONY: install run test lint evaluate-sample evaluate-full docker-build

install:
	uv sync

run:
	./run.sh

test:
	uv run pytest -q

lint:
	uv run ruff format --check .
	uv run ruff check .

# The sample keeps its own registry and output so it never mixes with the real run.
SAMPLE_DATA_DIR := data/sample_run

evaluate-sample:
	DATA_DIR=$(SAMPLE_DATA_DIR) uv run python -m kipu.pipeline --source dir:data/sample/input \
		--out $(SAMPLE_DATA_DIR)/output
	uv run python scripts/evaluate.py $(SAMPLE_DATA_DIR)/output --public data/sample \
		--tests data/sample/tests_sample.json --expected data/sample/expected_output

evaluate-full:
	uv run python -m kipu.pipeline --source http --out data/out_full
	uv run python scripts/evaluate.py data/out_full --public data/fuentes \
		--tests data/fuentes/tests_public.json

docker-build:
	docker build -t kipu-anonymizer .
