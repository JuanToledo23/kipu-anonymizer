#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: no se encontró .env en $ROOT" >&2
  exit 1
fi
set -a; source .env; set +a

: "${FUENTES_TOKEN:?ERROR: FUENTES_TOKEN no está definido en .env}"
: "${FUENTES_URL:?ERROR: FUENTES_URL no está definido en .env}"
FUENTES_URL="${FUENTES_URL%/}"

mkdir -p data/sample data/fuentes

# download <path> <dest>: stops with HTTP status + URL on failure, never prints the token
download() {
  local url="$FUENTES_URL/$1" dest="$2" status
  status=$(curl -sS -o "$dest" -w '%{http_code}' -H "Authorization: Bearer $FUENTES_TOKEN" "$url") || {
    echo "ERROR: curl falló para $url" >&2; exit 1; }
  if [[ "$status" -lt 200 || "$status" -ge 300 ]]; then
    echo "ERROR: HTTP $status al descargar $url" >&2
    rm -f "$dest"
    exit 1
  fi
  echo "OK  $status  $url -> $dest"
}

tmpzip="$(mktemp -t sample.XXXXXX).zip"
trap 'rm -f "$tmpzip"' EXIT
download sample.zip "$tmpzip"
unzip -oq "$tmpzip" -d data/sample

download record_index   data/fuentes/record_index.json
download tests/public   data/fuentes/tests_public.json
download chats/channels data/fuentes/channels.json
download docs           data/fuentes/docs_index.json
download tables         data/fuentes/tables_index.json

echo "== Validador =="
node validar.mjs data/sample/expected_output --public data/sample

echo "== Árbol de data =="
find data -maxdepth 3 | sort

rm -f muestra_para_chat.zip
zip -rq muestra_para_chat.zip data/sample data/fuentes
echo "== Zip creado: muestra_para_chat.zip ($(du -h muestra_para_chat.zip | cut -f1)) =="
