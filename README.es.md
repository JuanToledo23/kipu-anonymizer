# Kipu Anonymizer

[English](README.md) · **Español**

> Vende los datos sin vender a la gente: seudonimización consistente y verificable de chats de
> soporte, documentos y tablas.

Kipu Pay, una fintech colombiana, quiere vender su corpus de soporte al cliente para entrenar un
chatbot. El corpus está lleno de datos personales: nombres, cédulas, celulares, correos, placas,
direcciones y detalles que identifican a alguien sin nombrarlo. Este servicio regenera todo el
corpus cambiando cada persona real por una identidad inventada de la misma categoría, que se
mantiene igual entre registros, entre corridas y cuando las fuentes crecen. Solo cambian los
fragmentos detectados; cada registro conserva intacto el resto de su contenido. Se construyó para el reto
Frontier "Vende los datos sin vender a la gente".

## Resultados

| Evaluación oficial (set de pruebas oculto) | Puntaje | Notas |
|---|---:|---|
| Corrida 1: solo reglas | **81 / 95** | 92,5 / 100, **1.er lugar de 7 equipos** |
| Corrida 2: reglas + capa de IA | **146 / 156** | **cero fugas directas** desde esta corrida |
| Corrida 3: 100 % del set | **197 / 205** | fáciles 94/94 · difíciles 40/43 |

| Costo por regeneración completa (10.856 registros) | |
|---|---|
| Con capa de IA, caché fría | **≈ US$2**, **~9 min** (3,4 M tokens de entrada + 1,2 M de salida) |
| Solo reglas | **~6 s** |
| Nueva corrida con caché caliente | US$0, ~19 s |

Cuando las fuentes crecieron en 2.559 registros (la cartera de una empresa adquirida), ningún
seudónimo existente cambió.

## Arquitectura

Siete pasos, cada uno un módulo en `src/kipu/`. El principio de diseño: **los modelos proponen; el
código decide, reemplaza y verifica.**

1. **Ingesta** (`ingest/`). Descarga chats, documentos (PDF, Markdown, HTML) y tablas (CSV, XML)
   del API de fuentes. Lee cada uno completo, incluidos la metadata, los comentarios HTML y los
   nombres de archivo, y lo convierte a un solo esquema de registro; guarda una copia cruda en
   disco.
2. **Directorio de personas** (`directory/`). Arma la lista de quién existe desde las tablas de
   clientes y empleados y desde los usuarios de Slack, antes de leer texto libre. Cada persona
   recibe un id interno fijo en un registro SQLite.
3. **Detección** (`detect/`, `llm/`). Las reglas encuentran todo lo que tiene formato: cédulas,
   celulares, correos, placas, direcciones y cada variante conocida de un nombre. Luego dos
   modelos de IA proponen lo que las reglas no ven (terceros, apodos, datos en prosa). Una
   propuesta solo cuenta si su texto exacto existe en el registro y pasa las validaciones de
   forma.
4. **Identidad** (`identity/`). Decide a qué persona pertenece cada mención usando
   identificadores fuertes (cédula, correo, celular, id de cliente, uid de Slack) y union-find
   con restricciones de no-unión. Un modelo más fuerte solo desempata lo que la evidencia deja
   abierto, y el código verifica su respuesta.
5. **Seudónimos** (`pseudo/`). Genera para cada persona una identidad creíble, del mismo género y
   formato, sembrada con HMAC a partir de un secreto y el id de la persona. Cada seudónimo queda
   reservado en el registro, así que es único y estable en el tiempo.
6. **Reemplazo y generalización** (`replace/`). Cambia cada fragmento detectado en su lugar,
   conservando su forma (mayúsculas, tildes ausentes, separadores, agrupación de dígitos). La
   información que identifica sin nombrar (barrio, ocupación, año de vinculación) se vuelve
   menos precisa.
7. **Auditoría independiente** (`audit/`). Busca cada valor original en toda la salida con una
   copia de la normalización del propio jurado (tildes, letras parecidas, separadores). Si
   encuentra uno, reprocesa ese registro; nada se publica hasta que la auditoría sale limpia.

Página de diseño (diagrama y los siete módulos, inglés/español):
<https://juantoledo23.github.io/kipu-anonymizer/>. Pitch:
<https://juantoledo23.github.io/kipu-anonymizer/pitch.html>. Ambos están también en
[`docs/`](docs/README.md).

## Decisiones clave

- **Reglas primero, IA encima.** Solo reglas se queda corto en texto libre, y solo IA inventa y
  borra de más. Los modelos solo añaden lo que las reglas no vieron, y el servicio sigue
  produciendo una salida completa sin ellos.
- **La IA devuelve el texto exacto, nunca texto reescrito.** El código ubica cada fragmento,
  valida su forma y lo reemplaza en su lugar, así que cada registro conserva intacto todo lo
  demás.
- **Registro persistente y seudónimos deterministas.** Los ids de persona y los seudónimos viven
  en SQLite y nunca se reconstruyen. Seudónimos aleatorios por corrida habrían roto el dataset
  cuando crecieron las fuentes.
- **Auditor independiente con reproceso.** Un paso aparte barre la salida buscando cada valor
  original conocido, igual que el jurado. Una fuga se reprocesa, no solo se reporta.
- **Identidad por identificadores fuertes, nunca por el nombre.** Dos personas con el mismo
  nombre siguen siendo dos, y una persona listada dos veces (misma cédula y contacto) es una.

Detalle, costos y compromisos: [DECISIONS.md](DECISIONS.md) (en inglés, con resumen en español).
Cada defecto encontrado en el camino: [docs/failures.md](docs/failures.md).

## Inicio rápido

Requisitos: Python 3.13, [uv](https://docs.astral.sh/uv/) y Node ≥ 20 (para el validador oficial
`validar.mjs`).

```bash
cp .env.example .env     # PSEUDONYM_SECRET, FUENTES_URL, FUENTES_TOKEN; llaves de API para la capa de IA
make install             # uv sync
make run                 # servicio en http://localhost:9000 → curl localhost:9000/v1/health
make lint test           # ruff + pytest
```

Evaluación:

```bash
make evaluate-sample     # corpus de muestra (DATA_DIR y registro propios) + validador + tests + diff vs esperado
make evaluate-full       # API de fuentes en vivo + validador + tests públicos
make preflight           # barrido hostil de fugas de data/out_full contra el snapshot crudo más reciente
```

`LLM_ENABLED=false` corre solo reglas. `LLM_MAX_COST_USD` (5 por defecto) pone un tope por
corrida; al alcanzarlo, la corrida termina con las reglas. Todas las variables están documentadas
en [`.env.example`](.env.example).

**Despliegue.** Corre como imagen Docker (`make docker-build`) con un volumen persistente en
`/data` para el registro y la última salida. En producción corre en Fly.io con una sola máquina
siempre encendida; [docs/deploy.md](docs/deploy.md) cubre secretos, redespliegues, logs y la regla
de no desplegar nunca durante una corrida de evaluación.

## API

Se sirve bajo `/v1` (y en la raíz). Con `ENTRYPOINT_TOKEN` definido, toda ruta excepto `/health`
exige `Authorization: Bearer <token>`. El contrato es
[`api/participante.openapi.yaml`](api/participante.openapi.yaml).

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/health` | Estado, equipo, versión y modelos configurados. |
| `POST` | `/run` | Inicia una regeneración completa desde el API de fuentes y devuelve un `job_id` (202). |
| `GET` | `/run/{job_id}` | Estado de la corrida, conteo de registros, resultado de la auditoría, tokens y costo. |
| `GET` | `/output/{archivo}` | Uno de los cuatro archivos de la última corrida terminada (`chats.jsonl`, `docs.jsonl`, `tables.jsonl`, `entities.json`). |
| `GET` | `/entities` | El diccionario completo de seudónimos (`entities.json`), solo para el jurado. |
| `POST` | `/anonymize` | Anonimiza registros nuevos en vivo, con los mismos seudónimos de la corrida. |

Una corrida fallida nunca reemplaza la última salida buena, y las corridas sobreviven a los
reinicios.

## Estructura del repositorio

```
src/kipu/
  ingest/      cliente del API de fuentes, réplica local, lectores de PDF/MD/HTML/CSV/XML
  directory/   personas desde tablas maestras y usuarios de Slack; registro SQLite
  identity/    union-find con restricciones de no-unión; resolución de homónimos
  detect/      detectores regex, índice de nombres, gazetteer de términos conservados, cuasi-identificadores
  llm/         cliente de modelos y caché, extracción, verificación, pase de omisiones, generalización
  pseudo/      seudónimos sembrados con HMAC y listas de nombres colombianos
  replace/     reemplazo que conserva la forma y generalización (ocupación, barrio)
  audit/       copia de la normalización del validador; auditor de fugas
  anonymizer.py, pipeline.py, api.py, entities.py, config.py
scripts/       evaluador, barrido preflight, verificadores de identidad y cuasi-identificadores, vigía de fuentes
tests/         pruebas unitarias y de servicio; redteam/ casos adversarios; js/ arnés de paridad
docs/          página de diseño, pitch, guía de despliegue, bitácora de fallas
api/           contratos OpenAPI (del reto)
validar.mjs    validador oficial de la salida (del reto)
ENUNCIADO.md   enunciado del reto (del reto)
```

## Evaluación y verificación

| Herramienta | Qué verifica |
|---|---|
| `scripts/evaluate.py` | Corre `validar.mjs`, luego cada test local por familia (directa, oculta, falso positivo, homónimos, consistencia, indirecta) y opcionalmente un diff de campos contra una salida esperada. Sale con 1 ante cualquier falla. |
| `scripts/preflight.py` | Barrido hostil de fugas con la comparación del jurado: cada valor de las tablas maestras, cada nombre, handle, correo y celular de Slack, nombres en metadata de documentos, correos/celulares/cédulas/placas no declarados, formas en MAYÚSCULAS sin tildes. También valida la estructura contra `record_index.json`. |
| `tests/redteam/sweep_output.py` | Barrido red-team A1–A7 de una salida contra su snapshot crudo; `run_anonymize_cases.py` envía casos adversarios a `/anonymize` en vivo (minúsculas, correos deletreados, letras cirílicas, homónimos). |
| `scripts/quasi_check.py` | Para cada persona con fila maestra, busca registros donde barrio, ocupación y año (cargo, ciudad y año para empleados) siguen apareciendo juntos. Falla si algún registro tiene los tres. |
| `scripts/identity_audit.py` | Personas con el mismo nombre, usuarios de chat sin vincular, registros que le faltan a una entidad, formas declaradas por dos entidades y seudónimos que cambiaron frente a una línea base. Corre sobre una copia del registro. |
| `scripts/judge_identity.py` | Repite las familias de consistencia y homónimos del jurado sobre cada persona del registro, no solo las pocas que nombran los tests públicos. |

`make lint test` corre 155 pruebas automáticas, incluido un arnés de paridad que compara la
normalización en Python con el JavaScript del validador.

## Límites y lo que no se busca

- **Menciones sueltas ambiguas.** Un nombre o apellido solo, sin participante, identificador o
  contexto que lo ancle, puede quedar sin cambiar o asignarse al homónimo equivocado. Las reglas
  prefieren no tocar una palabra común antes que reemplazarla.
- **Sin anotación manual de cobertura.** La cobertura se mide contra cada valor que conocen las
  tablas maestras y Slack, contra los tests del reto y contra casos adversarios, no contra un
  corpus etiquetado a mano. Un tercero que solo aparece en prosa y que ambos modelos y el pase de
  omisiones pasan por alto no se contaría.
- **Seudonimización, no privacidad diferencial.** La salida conserva estructura real, fechas,
  montos y cuasi-identificadores generalizados, sin garantía formal contra ataques de enlace. El
  registro es una llave de re-identificación: se queda en el servidor y nunca hace parte de lo
  que se vende.
- **Modelos de terceros.** Con la capa de IA activa, el texto de los registros se envía a los
  proveedores de modelos (vía OpenRouter, con Anthropic como respaldo). Use `LLM_ENABLED=false`
  donde eso no sea aceptable.
- **La base legal está fuera de alcance.** Consentimiento, finalidad y cumplimiento de la ley
  colombiana de protección de datos (Ley 1581 de 2012) son decisiones del dueño de los datos, no
  de este servicio.

## Licencia y autor

[MIT](LICENSE) © 2026 Juan Toledo. Construido para el reto Frontier. El material del reto
(`ENUNCIADO.md`, `api/`, `validar.mjs`) pertenece a sus organizadores.
