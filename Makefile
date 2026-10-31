.PHONY: install run test lint evaluate-sample evaluate-full preflight docker-build

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

# Hostile sweep of an output dir against the raw corpus (newest data/raw/<run> by default).
PREFLIGHT_OUT ?= data/out_full
PREFLIGHT_RAW ?= $(shell ls -td data/raw/*/ 2>/dev/null | head -1)

preflight:
	uv run python scripts/preflight.py $(PREFLIGHT_OUT) --raw $(PREFLIGHT_RAW)

docker-build:
	docker build -t kipu-anonymizer .
