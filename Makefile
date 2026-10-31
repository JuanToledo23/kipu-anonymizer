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

evaluate-sample:
	uv run python -m kipu.pipeline --source dir:data/sample/input --out data/out_sample
	uv run python scripts/evaluate.py data/out_sample --public data/sample \
		--tests data/sample/tests_sample.json --expected data/sample/expected_output

evaluate-full:
	uv run python -m kipu.pipeline --source http --out data/out_full
	uv run python scripts/evaluate.py data/out_full --public data/fuentes \
		--tests data/fuentes/tests_public.json

docker-build:
	docker build -t kipu-anonymizer .
