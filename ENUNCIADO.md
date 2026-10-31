# Enunciado: Vende los datos sin vender a la gente

Reto Frontier · 27 de octubre de 2026 · versión editable en https://claude.ai/artifact/GhF6KEpmUYo2KsdgLiHLXQ

## La historia

Kipu Pay es una fintech colombiana de crédito y pagos. Tiene tres años de conversaciones de soporte, documentos internos y tablas de clientes y transacciones, y un comprador dispuesto a pagar por ese corpus para entrenar modelos de atención al cliente. El trato solo se cierra si los datos siguen siendo útiles para entrenar y ninguna persona real queda expuesta.

Tu equipo tiene que entregar esa versión del corpus: la misma información de negocio, las mismas conversaciones, pero sin que nadie pueda saber quién es quién. No basta con tachar cédulas. Hay nombres en mayúsculas sin tildes dentro de extractos, correos deletreados con "arroba" y "punto", placas, pasaportes, metadatos de PDF, comentarios HTML, menciones de Slack, dos clientas con el mismo nombre completo, una agente que se llama igual que una clienta, y combinaciones de barrio, ocupación y año que identifican a alguien sin decir su nombre.

El jurado es un programa. Puntúa tres cosas: que no se fugue nada, que lo que queda siga sirviendo, y que cada persona sea una sola persona de punta a punta.

## Qué recibes

Todo llega por el API de fuentes (sección más abajo) con tu token, que ves en `/evento/reto` de Frontier junto con la URL del API.

| Qué                 | Dónde                                                                                                                         | Formato                                                                                                        | Tamaño                               |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Chats de soporte    | `chats/<canal>/<fecha>.json`, `chats/users.json`, `chats/channels.json`                                                       | Export estilo Slack: mensajes con `user` (uid), `text`, `ts`, `client_msg_id`, adjuntos, reacciones, ediciones | 320 hilos, 2.028 mensajes, 6 canales |
| Documentos internos | `docs/<archivo>`, `docs/index.json`                                                                                           | 11 PDF, 6 HTML, 9 Markdown con front matter; 2 en inglés                                                       | 26 documentos                        |
| Tablas              | `tables/clientes.csv` (Latin-1, separador `;`), `tables/transacciones.csv` (UTF-8), `tables/pqrs.xml`, `tables/empleados.xml` | CSV y XML tal como salen de los sistemas                                                                       | 6.243 filas                          |
| Índice de registros | `record_index.json`                                                                                                           | Lista de todos los ids que tu salida debe contener, por fuente                                                 | 8.297 ids                            |
| Tests públicos      | `tests_public.json`                                                                                                           | 5 tests con el registro, el dato y el resultado esperado                                                       | 1 por dimensión                      |
| Muestra             | `sample/`                                                                                                                     | Un dataset chico con otras personas y su salida ideal completa en `expected_output/`                           | 13 tests                             |
| Validador           | `node validar.mjs` (en este paquete)                                                                                          | Programa que comprueba que tu `output/` tiene la forma correcta antes de correr                                |                                      |

Los ids de registro salen de las fuentes: en chats es el `client_msg_id` de cada mensaje; en documentos el `id` de `index.json`; en tablas es `<tabla>:<llave>` (`clientes:CL-12538`, `transacciones:TX-000158`, `pqrs:PQR-2026-0451`, `empleados:KP-005`).

El corpus pesa unos 435 mil tokens. No cabe en una ventana de contexto y no se cura a mano en el tiempo del reto.

## API de fuentes

Un solo servidor, una raíz `/v1`, cabecera `Authorization: Bearer <tu token>`. Los archivos binarios y los CSV viajan tal cual, con su `Content-Type` y su charset reales: `clientes.csv` llega en Latin-1 con `;`, `transacciones.csv` en UTF-8 con `,`. El contrato completo está en `api/fuentes.openapi.yaml`.

| Método y ruta                | Devuelve                                                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `GET /chats/channels`        | `channels.json`: id, nombre, propósito y miembros (uids) de cada canal                                                   |
| `GET /chats/users`           | `users.json`: perfiles parciales; los clientes no traen customer_id ni celular                                           |
| `GET /chats/{canal}`         | las fechas con mensajes en ese canal                                                                                     |
| `GET /chats/{canal}/{fecha}` | los mensajes de ese día, formato export de Slack (`user`, `text`, `ts`, `client_msg_id`, `files`, `reactions`, `edited`) |
| `GET /docs`                  | `index.json`: id, filename y formato de los 26 documentos                                                                |
| `GET /docs/{id}`             | el archivo original (PDF binario, HTML o Markdown)                                                                       |
| `GET /tables`                | nombre, formato, charset y separador de las cuatro tablas                                                                |
| `GET /tables/{nombre}`       | los bytes originales de `clientes.csv`, `transacciones.csv`, `pqrs.xml` o `empleados.xml`                                |
| `GET /record_index`          | todos los ids que tu salida debe contener, por fuente                                                                    |
| `GET /tests/public`          | los cinco tests públicos                                                                                                 |
| `GET /sample.zip`            | el dataset de muestra con su salida ideal                                                                                |

Límite de 600 peticiones por minuto por token. Los JSON llevan `ETag`: con `If-None-Match` recibes 304 si nada cambió.

## Qué entregas

Tu solución es un servicio HTTP corriendo en una URL pública. Puedes construirlo y desplegarlo a tu ritmo: nada lo evalúa hasta que tú lo pides. Cuando esté listo, pega la URL en `/evento/reto` de Frontier y dale a VALIDAR ENTRYPOINT. Primero se comprueba `GET /health` y un `POST /anonymize` con un registro de la muestra; si eso falla, la corrida no se gasta. Si pasa, arranca una corrida de evaluación. Tienes tres y decides cuándo usar cada una: la primera mide el 60 % de los tests ocultos, la segunda el 75 % y la tercera el 100 %. En cada corrida el servicio de evaluación llama tu API, le pide que regenere la salida contra el API de fuentes, descarga `output/`, corre el validador y los tests, y hace pruebas en vivo con registros que no has visto. Al terminar, `/evento/reto` muestra el puntaje de 0 a 100 de esa corrida. Además entregas el repositorio con el pipeline y un `run.sh` que levanta el servicio, un `DECISIONS.md` y una demo de cinco minutos.

`output/` tiene exactamente cuatro archivos. Cada registro de entrada aparece una vez, con el mismo `id`; ninguna clave desaparece (la metadata del PDF sale con `author` seudonimizado, no sin `author`); los nombres de archivo y adjuntos se conservan como campos y se anonimizan como texto.

| Archivo         | Un registro por línea | Campos                                                                                                                                                                                                                                                                                                                           |
| --------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `chats.jsonl`   | un mensaje            | `id` (= `client_msg_id`), `thread_id` (`<canal>:<ts del primer mensaje>`), `author` (nombre, no uid), `ts`, `text`, `attachments` (lista de `{filename}`)                                                                                                                                                                        |
| `docs.jsonl`    | un documento          | `id`, `format` (`pdf`/`html`/`md`), `filename`, `title`, `body` (texto en markdown, con front matter o comentarios HTML si los había), `metadata` (objeto: author, subject, keywords, meta de HTML)                                                                                                                              |
| `tables.jsonl`  | una fila              | `id` (`<tabla>:<llave>`), `table`, `columns` (objeto con todas las columnas originales, valores como texto)                                                                                                                                                                                                                      |
| `entities.json` | el diccionario        | `{"entities": [...]}`: por cada persona `entity_id`, `type`, `originals` (todas las formas reales que encontraste), `pseudonyms` (`name` y, si aplica, `email`, `phone`, `cedula`, `address`, `plate`...), `records` (ids donde aparece); por cada organización, lugar o figura pública que conservaste, `kept: true` y `reason` |

El diccionario es solo para el jurado: contiene las formas originales y nunca viaja con el dataset. Es la pieza que permite medir consistencia y honestidad, y la que más equipos entregan incompleta.

Así se ve un antes y después tomado de `sample/`:

| Fuente    | Entrada                                                                                                                   | Salida                                                                                                                                                  |
| --------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Chat      | `user: U0V6WYVYG`, `text: "Hola, me llamo Manuela Castaño. Me están cobrando una cuota que ya pagué."`                    | `author: "Sofía Elena Zambrano"`, `text: "Hola, me llamo Sofía Elena Zambrano. Me están cobrando una cuota que ya pagué."`                              |
| Documento | front matter `author: <correo real>`, cuerpo "Preparado por <nombre real>, Contadora" y una tabla con nombre y `CL-88627` | `author: doris.lozano@kipupay.co`, "Preparado por Doris Lozano, Contadora", misma tabla con el seudónimo y el mismo `CL-88627`                          |
| Tabla     | fila de `clientes.csv` con nombre, cédula, correo, celular, dirección, fecha de nacimiento, ocupación "periodista"        | misma fila, mismo `cliente_id`, seudónimos con formato válido, ocupación generalizada a "profesional de medios", ciudad y fecha de vinculación intactas |

La ciudad, el barrio, la ocupación o el año no son PII por sí solos. Lo son cuando juntos señalan a una sola persona; ahí se generalizan (barrio a ciudad, ocupación a sector, año a "hace unos años"), no se borran.

`DECISIONS.md` responde tres preguntas: qué consideraron PII, qué conservaron a propósito y por qué, y cuánto costó correr el pipeline en tokens y tiempo.

## API que expone tu solución

Tu pipeline corre como un servicio HTTP en la URL que guardas en Frontier. En cada corrida, el servicio de evaluación lo llama en tres momentos: para regenerar la salida desde cero, para descargarla y para probar, con registros que nunca viste, que tus seudónimos son consistentes. Sin autenticación, todo en UTF-8, raíz `/v1`, accesible desde internet. El contrato completo está en `api/participante.openapi.yaml`.

| Método y ruta           | Qué hace                                                                                                                        | Límite                    |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| `GET /health`           | `{status, team, version, models}`: qué modelos o APIs usa el pipeline, si alguno                                                |                           |
| `POST /run`             | recibe `{sources_url, token}` y regenera `output/` desde el API de fuentes; devuelve un `job_id`                                | 2 horas                   |
| `GET /run/{job_id}`     | estado (`queued`, `running`, `done`, `failed`), conteo de registros y costo en tokens y segundos                                |                           |
| `GET /output/{archivo}` | `chats.jsonl`, `docs.jsonl`, `tables.jsonl` o `entities.json` de la última corrida terminada                                    |                           |
| `POST /anonymize`       | recibe `{source, records}` en el esquema normalizado y devuelve los mismos ids anonimizados más las entidades que intervinieron | 50 registros, 60 segundos |
| `GET /entities`         | el diccionario completo de la última corrida; solo lo llama el jurado                                                           |                           |

`/anonymize` es la prueba en vivo. El jurado manda un mensaje nuevo que nombra a una clienta que ya está en tu salida y espera el mismo seudónimo que le diste en `output/`; manda una persona nueva y espera un seudónimo nuevo, único y de la misma categoría; manda dos personas con el mismo nombre y espera dos seudónimos. También es lo que hace vendible la demo: anonimizar en vivo lo que el público escriba.

## Las fuentes van a crecer

En algún momento del reto, sin aviso previo de cuándo, el API de fuentes empieza a servir más datos: fechas nuevas en los canales, canales nuevos, documentos nuevos, filas nuevas en las tablas y un `record_index` más grande. Nada de lo anterior se borra ni cambia. Es la misma API y el mismo esquema; solo hay más.

Cada corrida se puntúa con las fuentes que el API servía cuando la pediste. Tu última corrida antes del cambio vale el 30 % del total y tu última corrida después, el 70 %. Si no corriste antes del cambio, el 30 % queda en cero. Tres reglas para las corridas después del cambio:

- Tu servicio regenera la salida completa, con todos los registros viejos y nuevos, desde el API de fuentes.
- Toda persona que ya estaba en tu salida conserva exactamente el mismo seudónimo. Se mide comparando el `entities.json` de tu última corrida antes del cambio con el de después, y multiplica los puntos de consistencia. Guarda tu mapa de seudónimos o hazlos deterministas; regenerarlos al azar en cada corrida destruye el dataset incremental que el comprador quiere.
- Las personas nuevas reciben seudónimos nuevos y únicos. Una persona vieja que aparece con un identificador nuevo sigue siendo la misma persona. Una persona nueva que se llama como una vieja es otra persona.

Los tests de la primera corrida se vuelven a correr sobre la segunda, más los nuevos sobre lo que llegó.

## Cómo se puntúa

El puntaje es automático, de 0 a 100, y sale de correr tu `output/` contra los 5 tests públicos y la parte de los 150 ocultos que mide esa corrida (60, 75 o 100 %), más unas medidas globales sobre todo el dataset. Después del cambio de fuentes los ocultos son 200. La salida ideal da 100. Una solución sólida por reglas, sin modelos, da alrededor de 88; hashear todo da 58.

| Dimensión    | Puntos | Qué mide                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fuga         | 45     | 35 por tests puntuales: un dato real que debía desaparecer sigue en su registro, en otro registro, en un metadato o en un nombre de archivo, o como hash sin sal; una combinación de barrio, ocupación y año que identifica a alguien sigue junta (se evalúa también contra la fila del maestro). 10 por un barrido global de todas las formas reales en toda la salida: cambiar una letra, pegar un dígito o esconder un nombre en una clave no sirve.                                              |
| Utilidad     | 35     | 10 por parecido del texto no personal con la salida ideal; 9 por falsos positivos intactos (bancos, marcas, lugares y figuras públicas que debían quedarse); 8 por categoría del seudónimo (nombre por nombre del mismo género, correo por correo, cédula con formato válido, lejos del original); 8 por estructura: las llaves entre tablas siguen uniendo las mismas filas, cada persona conserva su número de registros bajo un solo seudónimo, y quién habló con quién en cada hilo se mantiene. |
| Consistencia | 20     | 8 por misma persona, mismo seudónimo en todas las fuentes (multiplicado por la unicidad de tus seudónimos); 5 por homónimos: dos personas con el mismo nombre reciben dos seudónimos; 4 por la relación "reporta a" entre empleados; 3 por honestidad del diccionario: que `entities.json` declare lo que de verdad había en la entrada.                                                                                                                                                             |

Un registro vaciado o reescrito por completo no pasa ningún test de fuga: se mide que el texto siga ahí. Los tests ocultos usan mecanismos distintos de los cinco públicos; no se publica más porque cada test público es una trampa que deja de ser oculta.

## Reglas

- Puedes usar cualquier modelo, librería o herramienta, incluidos LLMs por API. No puedes usar servicios de anonimización de PII como producto terminado (AWS Comprehend PII, Presidio como servicio, Private AI y similares) ni librerías open source que hagan ese trabajo. Queremos ver el diseño del pipeline, no quién tiene mejor tarjeta de crédito.
- La salida es uno a uno con la entrada: cada registro aparece exactamente una vez, con su mismo `id`. Borrar registros o vaciarlos cuenta como pérdida de utilidad y no pasa los tests de fuga.
- Seudonimizar no es opcional. La misma persona recibe el mismo seudónimo en todas las fuentes; personas distintas con el mismo nombre reciben seudónimos distintos. Reemplazar todo por `[REDACTED]` destruye las relaciones y penaliza utilidad.
- El seudónimo conserva la categoría del original: un nombre por un nombre del mismo género y región, un correo por un correo, una cédula por una cédula con formato válido, y lejos del original. Un hash o un "John Doe" pasa el test de fuga y pierde utilidad.
- Organizaciones, lugares, marcas y figuras públicas se conservan: Bancolombia, Carulla, Plaza Botero y Shakira no son personas naturales. Un apellido que también es un lugar sigue siendo apellido cuando nombra a una persona.
- Tu servicio debe regenerar la salida desde cero contra el API de fuentes cada vez que el evaluador se lo pida, en menos de dos horas, y responder `/anonymize` en menos de un minuto por lote. Si tu servicio deja de responder durante una corrida, la corrida termina sin puntaje y cuenta como usada.
- No recibes el ground truth ni el catálogo de tests ocultos. Recibes los cinco tests públicos y la muestra con su salida ideal.
- Tiempo: el mismo bloque del hackathon que el reto de agentes. Tienes tres corridas de evaluación; cada validación que pasa gasta una.

## Errores comunes

Todos estos los medimos antes del reto con el mismo runner que te va a puntuar.

| Lo que parece una solución                                                            | Qué pasa                                                                                                                                   | Puntaje                   |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------- |
| Hashear cada dato con SHA-256                                                         | La cédula es reversible por fuerza bruta, dos personas con el mismo nombre dan el mismo hash, y nada conserva categoría ni estructura      | 58                        |
| Reemplazar todo por `[REDACTED]`                                                      | Pasa la fuga directa y pierde homónimos, "reporta a", grados por persona y parecido con el texto                                           | 56                        |
| Un seudónimo por género ("Marcela Durán" para todas, "Hernán Ocampo" para todos)      | Fusiona a 150 personas en dos: la consistencia y la estructura se van a cero                                                               | 70                        |
| Cambiar una letra o un dígito de cada forma real ("Valentina Martines", "3001234568") | El barrido global tolera confusables, separadores e invisibles; cuenta como fuga en casi todos los tests                                   | 41                        |
| Detectar el 70 % de las personas y dejar el resto                                     | Cada persona no detectada es una fuga en cada uno de sus registros; con homónimos, además fusiona                                          | 76                        |
| Vaciar los registros difíciles                                                        | Un registro destruido no pasa ningún test de fuga y resta parecido                                                                         | menos que dejarlo intacto |
| Declarar en `entities.json` menos formas de las que había                             | La honestidad del diccionario se mide contra la verdad, no contra lo que declaras                                                          | hasta 3 puntos menos      |
| Resolver quién es quién solo por el autor del mensaje                                 | El bot dice el nombre completo del agente en el handoff, los agentes repiten correos y celulares de clientes, y el texto nombra a terceros | fuga en chats             |
| Decidir homónimos por la ciudad o el cargo que aparecen junto al nombre               | No aparecen. El desempate vive en otra fuente: un radicado, una fecha y un monto, un adjunto, una fila del maestro                         | homónimos                 |
| Romper el cuasi-identificador solo en el chat                                         | La fila de `clientes.csv` sigue diciendo contadora, Barranquilla y 2024; la indirecta se evalúa sobre la unión                             | indirecta                 |
| Reescribir el texto con un modelo                                                     | Pierde parecido con la salida ideal y puede inventar o borrar datos de negocio; se reemplaza, no se redacta de nuevo                       | utilidad                  |

## Antes de entregar

El validador viene en este paquete (`validar.mjs`, Node 20 o más nuevo) y es el mismo que corre el servicio de evaluación sobre lo que descarga de tu `/output`. Una salida que no pasa el validador gasta la corrida sin puntaje. Pruébalo primero con la muestra que baja `GET /sample.zip`:

```bash
node validar.mjs sample/expected_output --public sample
```

Debe decir `OK: la salida pasa el validador`. Luego con tu salida, apuntando `--public` a la carpeta donde guardaste el `record_index.json` del API:

```bash
node validar.mjs output/ --public fuentes/
```

Si falla, imprime `FALLA: n problema(s)` y una línea por problema. Las que más salen:

| Mensaje                                                                         | Qué significa                                                  |
| ------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `chats: faltan 12 registros` / `sobran 3 registros`                             | tu salida no es uno a uno con `record_index.json`              |
| `tables:clientes:CL-12538 perdió columnas ['notas']`                            | una clave desapareció; se reemplaza el valor, no la clave      |
| `docs:doc_02 añadió claves de metadata ['x_team']`                              | la metadata sale con las mismas claves que entró               |
| `entities[p_031] el seudónimo email='...' no aparece en ninguno de sus records` | declaraste un seudónimo que no usaste                          |
| `entities[p_031] la forma original 'Camila Arango' sigue en la salida`          | el diccionario dice que la reemplazaste y el texto dice que no |
| `entities[p_031] no conservada y sin 'pseudonyms'`                              | toda persona que no se conserva necesita al menos `name`       |

Las líneas de columnas o claves de metadata perdidas y añadidas las calcula el servicio de evaluación comparando con la entrada, así que salen en el detalle de la corrida y la corrida se puntúa igual.

Pasar el validador no da puntos: dice que tu salida tiene la forma correcta y que el diccionario es coherente con ella. Los puntos salen de los tests, y los tests públicos en `tests_public.json` te dicen cómo se lee cada uno: el registro, el dato y lo que debe pasar con él.
