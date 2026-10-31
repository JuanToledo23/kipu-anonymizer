FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=9000

WORKDIR /app
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY run.sh ./
RUN uv sync --frozen --no-dev

USER app
VOLUME /data
EXPOSE 9000
CMD ["./run.sh"]
