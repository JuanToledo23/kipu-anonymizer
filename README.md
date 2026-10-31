# Vende los datos sin vender a la gente

Paquete de arranque del reto de anonimización. El enunciado completo está en `ENUNCIADO.md`.

| Archivo                         | Qué es                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `ENUNCIADO.md`                  | la historia, las fuentes, el formato de `output/`, el API que expone tu servicio y cómo se puntúa      |
| `api/fuentes.openapi.yaml`      | el contrato del API de fuentes, de donde bajas los chats, documentos y tablas                          |
| `api/participante.openapi.yaml` | el contrato que tiene que cumplir tu servicio: `/health`, `/run`, `/output`, `/anonymize`, `/entities` |
| `validar.mjs`                   | el validador de `output/`, el mismo que corre el servicio de evaluación                                |

## Primeros pasos

1. En `/evento/reto` de Frontier copia la URL del API de fuentes y tu token.
2. Baja la muestra: `curl -H "Authorization: Bearer $TOKEN" $FUENTES/sample.zip -o sample.zip` y descomprímela en `sample/`.
3. Corre el validador sobre la salida ideal de la muestra con Node 20 o más nuevo:
   `node validar.mjs sample/expected_output --public sample`. Tiene que decir `OK: la salida pasa el validador`.
4. Baja `GET /record_index` a `fuentes/record_index.json` y valida tu salida con
   `node validar.mjs output/ --public fuentes/`.
5. Despliega tu servicio en una URL pública. Cuando quieras una evaluación, pega la URL en `/evento/reto` y dale a
   VALIDAR ENTRYPOINT: si responde `/health` y `/anonymize`, arranca una de tus tres corridas.

## Systems design

Publicado en GitHub Pages desde `docs/` (Settings → Pages → main, /docs).
