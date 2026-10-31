// juez/validar-cli.ts
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

// juez/texto.ts
var ESPACIO = "\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
var PALABRA = "\\p{L}\\p{N}_";
var CONFUSABLES = {
  \u0430: "a",
  \u0435: "e",
  \u043E: "o",
  \u0440: "p",
  \u0441: "c",
  \u0445: "x",
  \u0443: "y",
  \u0456: "i",
  \u0458: "j",
  \u0455: "s",
  \u0410: "A",
  \u0415: "E",
  \u041E: "O",
  \u0420: "P",
  \u0421: "C",
  \u0425: "X",
  \u0423: "Y",
  \u0406: "I",
  \u0408: "J",
  \u0405: "S",
  \u0412: "B",
  \u041D: "H",
  \u041A: "K",
  \u041C: "M",
  \u0422: "T",
  \u03BF: "o",
  \u03B1: "a",
  \u03C1: "p",
  \u03BD: "v",
  \u03B9: "i",
  \u2113: "l",
  \u0131: "i",
  \u0251: "a"
};
var RX_CONFUSABLE = new RegExp(`[${Object.keys(CONFUSABLES).join("")}]`, "gu");
var RX_FORMATO = new RegExp("\\p{Cf}", "u");
var RX_ESPACIO_RARO = /[\p{Zs}\t\r\n\v\f]/u;
var RX_ESPACIOS = / {2,}/g;
var RX_MARCA = new RegExp("\\p{Mn}", "gu");
var RX_SEPARADOR_DIGITOS = new RegExp(`(?<=\\p{Nd})[${ESPACIO}.,'\u2019\`\xB4\xB7/\\-\u2013\u2014_](?=\\p{Nd})`, "gu");
var RX_NO_DIGITO = new RegExp("\\P{Nd}", "gu");
var RX_TEXTO = new RegExp(`^[${PALABRA} .'_-]+$`, "u");
var RX_TOKENS = new RegExp(`[${PALABRA}]+|[^${PALABRA}${ESPACIO}]`, "gu");
var RX_BLANCOS = new RegExp(`[${ESPACIO}]+`, "u");
function escaparRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
}
function partir(s) {
  return s.split(RX_BLANCOS).filter((x) => x !== "");
}
function largo(s) {
  let n = 0;
  for (const _ of s) n++;
  return n;
}
function plegar(s) {
  return s.toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "\u03C3").replace(/ſ/g, "s");
}
function quitarTildes(s) {
  return s.normalize("NFD").replace(RX_MARCA, "");
}
function clean(s) {
  const base = s.normalize("NFKC").replace(RX_CONFUSABLE, (c) => CONFUSABLES[c] ?? c);
  let out = "";
  for (const c of base) {
    if (RX_FORMATO.test(c)) continue;
    out += RX_ESPACIO_RARO.test(c) ? " " : c;
  }
  return out.replace(RX_ESPACIOS, " ");
}
var cacheNorm = /* @__PURE__ */ new Map();
function norm(s) {
  let r = cacheNorm.get(s);
  if (r === void 0) {
    r = plegar(quitarTildes(clean(s)));
    if (cacheNorm.size > 2e5) cacheNorm.clear();
    cacheNorm.set(s, r);
  }
  return r;
}
var cacheCompacto = /* @__PURE__ */ new Map();
function digitosCompactos(s) {
  let r = cacheCompacto.get(s);
  if (r === void 0) {
    r = clean(s).replace(RX_SEPARADOR_DIGITOS, "");
    if (cacheCompacto.size > 2e5) cacheCompacto.clear();
    cacheCompacto.set(s, r);
  }
  return r;
}
function soloDigitos(s) {
  return s.replace(RX_NO_DIGITO, "");
}
var cacheRx = /* @__PURE__ */ new Map();
function rx(fuente, flags = "u") {
  const clave = `${flags}/${fuente}`;
  let r = cacheRx.get(clave);
  if (!r) {
    r = new RegExp(fuente, flags);
    if (cacheRx.size > 5e4) cacheRx.clear();
    cacheRx.set(clave, r);
  }
  return r;
}
function digitosEn(haystack, digitos) {
  const compacto = digitosCompactos(haystack);
  const candidatos = /* @__PURE__ */ new Set([digitos]);
  if (digitos.length === 12 && digitos.startsWith("57")) candidatos.add(digitos.slice(2));
  for (const d of candidatos)
    if (rx(`(?<!\\p{Nd})(?:57)?${escaparRegex(d)}(?!\\p{Nd})`).test(compacto)) return true;
  return false;
}
function contiene(haystack, needle, opciones = {}) {
  if (!needle) return false;
  if (opciones.exacto) return haystack.includes(needle);
  const digitos = soloDigitos(needle);
  if (digitos && largo(digitos) >= 6 && largo(digitos) >= 0.6 * largo(needle) && !/[a-zA-Z]/.test(needle))
    return digitosEn(haystack, digitos);
  const h = norm(haystack);
  const n = norm(needle);
  if (RX_TEXTO.test(n) && /[a-z]/.test(n)) {
    if (rx(`(?<![a-z0-9])${escaparRegex(n)}(?![a-z0-9])`).test(h)) return true;
    const palabras = partir(n);
    if (palabras.length >= 2 && palabras.every((w) => largo(w) >= 3))
      return rx(
        `(?<![a-z0-9])${palabras.map(escaparRegex).join(`[${ESPACIO}]*`)}(?![a-z0-9])`
      ).test(h);
    return false;
  }
  return h.includes(n);
}

// juez/esquema.ts
var FUENTES = ["chats", "docs", "tables"];
var OBLIGATORIOS = {
  chats: ["id", "thread_id", "author", "ts", "text", "attachments"],
  docs: ["id", "format", "filename", "title", "body", "metadata"],
  tables: ["id", "table", "columns"]
};
var esObjeto = (v) => typeof v === "object" && v !== null && !Array.isArray(v);
function verdadero(v) {
  if (v === null || v === void 0 || v === false || v === 0 || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  if (esObjeto(v)) return Object.keys(v).length > 0;
  return true;
}
function lista(v) {
  if (Array.isArray(v)) return v;
  if (typeof v === "string") return Array.from(v);
  if (esObjeto(v)) return Object.keys(v);
  return [];
}
function textos(v) {
  return lista(v).filter((x) => typeof x === "string");
}
function repr(v) {
  if (typeof v === "string") {
    if (v.includes("'") && !v.includes('"')) return `"${v.replace(/\\/g, "\\\\")}"`;
    return `'${v.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
  }
  if (Array.isArray(v)) return `[${v.map(repr).join(", ")}]`;
  return String(v);
}
function ordenar(xs) {
  return [...xs].sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
}
function registro(o, rid) {
  for (const f of FUENTES) {
    const r = o[f].get(rid);
    if (r && Object.keys(r).length > 0) return r;
  }
  return null;
}
function todos(o) {
  return FUENTES.flatMap((f) => [...o[f].values()].map((r) => [f, r]));
}
function plano(v) {
  if (v === null || v === void 0) return [];
  if (typeof v === "string") return [v];
  if (typeof v === "boolean") return [v ? "True" : "False"];
  if (typeof v === "number") return [String(v)];
  if (Array.isArray(v)) return v.flatMap(plano);
  if (esObjeto(v)) return Object.entries(v).flatMap(([k, x]) => [k, ...plano(x)]);
  return [String(v)];
}
function textoDeRegistro(rec, campo) {
  if (campo === void 0 || campo === null || ["", "any", "raw", "*"].includes(campo)) {
    const sinId = Object.fromEntries(Object.entries(rec).filter(([k]) => k !== "id"));
    return plano(sinId).join(" | ");
  }
  if (campo.startsWith("columns.")) {
    const col = campo.split(".").slice(1).join(".");
    const cols = esObjeto(rec.columns) ? rec.columns : {};
    if (col === "*") return plano(cols).join(" | ");
    return plano(cols[col]).join(" | ");
  }
  return plano(rec[campo]).join(" | ");
}
function leerJsonl(nombre, contenido, errores) {
  if (contenido === null) {
    errores.push(`falta ${nombre}`);
    return [];
  }
  const filas = [];
  contenido.split("\n").forEach((linea, i) => {
    const l = linea.trim();
    if (!l) return;
    try {
      filas.push(JSON.parse(l));
    } catch {
      errores.push(`${nombre}:${i + 1} no es JSON v\xE1lido`);
    }
  });
  return filas;
}
function cargarSalida(archivos) {
  const o = {
    chats: /* @__PURE__ */ new Map(),
    docs: /* @__PURE__ */ new Map(),
    tables: /* @__PURE__ */ new Map(),
    entities: {},
    errores: []
  };
  for (const f of FUENTES) {
    for (const r of leerJsonl(`${f}.jsonl`, archivos[f], o.errores)) {
      const rid = esObjeto(r) ? r.id : void 0;
      if (typeof rid !== "string") {
        o.errores.push(`${f}.jsonl: registro sin id`);
        continue;
      }
      if (o[f].has(rid)) o.errores.push(`${f}.jsonl: id repetido ${rid}`);
      o[f].set(rid, r);
    }
  }
  if (archivos.entities === null) o.errores.push("falta entities.json");
  else
    try {
      o.entities = JSON.parse(archivos.entities);
    } catch {
      o.errores.push("entities.json no es JSON v\xE1lido");
    }
  return o;
}
function listaConservada(ents) {
  return ents.filter((e) => verdadero(e.kept)).flatMap((e) => textos(e.originals ?? [])).filter((x) => Array.from(x).length >= 4 && new RegExp("\\p{L}", "u").test(x));
}
function estaConservada(orig, conservados) {
  return conservados.some((k) => contiene(k, orig));
}
function esNombreSuelto(s) {
  const primero = Array.from(s)[0] ?? "";
  return !s.includes(" ") && !s.includes("@") && !new RegExp("\\p{Nd}", "u").test(s) && primero !== "" && primero.toUpperCase() === primero && primero.toLowerCase() !== primero;
}
function sinRepetir(errores) {
  return [...new Set(errores)];
}
function validar(o, indice, entrada = null) {
  const errs = [...o.errores];
  for (const f of FUENTES) {
    const esperados = new Set(indice[f] ?? []);
    const llegaron = new Set(o[f].keys());
    const faltan = ordenar([...esperados].filter((x) => !llegaron.has(x)));
    const sobran = ordenar([...llegaron].filter((x) => !esperados.has(x)));
    if (faltan.length)
      errs.push(`${f}: faltan ${faltan.length} registros, p. ej. ${repr(faltan.slice(0, 3))}`);
    if (sobran.length)
      errs.push(`${f}: sobran ${sobran.length} registros, p. ej. ${repr(sobran.slice(0, 3))}`);
    for (const [rid, r] of o[f]) {
      for (const k of OBLIGATORIOS[f])
        if (!(k in r)) errs.push(`${f}:${rid} sin campo obligatorio '${k}'`);
      if (f === "chats" && "attachments" in r && !Array.isArray(r.attachments))
        errs.push(`chats:${rid} attachments debe ser lista`);
      if (f === "docs" && "metadata" in r && !esObjeto(r.metadata))
        errs.push(`docs:${rid} metadata debe ser objeto`);
      if (f === "tables" && "columns" in r && !esObjeto(r.columns))
        errs.push(`tables:${rid} columns debe ser objeto`);
    }
  }
  if (entrada && Object.keys(entrada).length > 0) {
    for (const [rid, inp] of Object.entries(entrada)) {
      const out = registro(o, rid);
      if (!out) continue;
      const claves = (v) => new Set(lista(v).map(String));
      const resta = (a, b) => ordenar([...a].filter((x) => !b.has(x)));
      if ("metadata" in inp && esObjeto(out.metadata)) {
        const perdidas = resta(claves(inp.metadata), claves(out.metadata));
        if (perdidas.length) errs.push(`docs:${rid} perdi\xF3 claves de metadata ${repr(perdidas)}`);
      }
      if ("columns" in inp && esObjeto(out.columns) && esObjeto(inp.columns)) {
        const perdidas = resta(claves(inp.columns), claves(out.columns));
        if (perdidas.length) errs.push(`tables:${rid} perdi\xF3 columnas ${repr(perdidas)}`);
        const nuevas = resta(claves(out.columns), claves(inp.columns));
        if (nuevas.length) errs.push(`tables:${rid} a\xF1adi\xF3 columnas ${repr(nuevas)}`);
        for (const [k, v] of Object.entries(inp.columns))
          if (k in out.columns && typeof v === "string" && typeof out.columns[k] !== "string")
            errs.push(`tables:${rid} la columna ${k} cambi\xF3 de tipo (era texto)`);
      }
      if ("metadata" in inp && esObjeto(out.metadata)) {
        const nuevas = resta(claves(out.metadata), claves(inp.metadata));
        if (nuevas.length) errs.push(`docs:${rid} a\xF1adi\xF3 claves de metadata ${repr(nuevas)}`);
      }
      if ("attachments" in inp && Array.isArray(out.attachments)) {
        if (lista(inp.attachments).length !== out.attachments.length)
          errs.push(`chats:${rid} cambi\xF3 el n\xFAmero de adjuntos`);
      }
    }
  }
  const ents = esObjeto(o.entities) ? o.entities.entities : void 0;
  if (ents === void 0 || ents === null) {
    errs.push("entities.json debe ser un objeto con la lista 'entities'");
    return sinRepetir(errs);
  }
  const crudas = lista(ents);
  crudas.forEach((e, i) => {
    if (!esObjeto(e)) errs.push(`entities[${i}] no es un objeto`);
  });
  const listaEnts = crudas.filter(esObjeto);
  const ids = new Set(FUENTES.flatMap((f) => [...o[f].keys()]));
  const vistos = /* @__PURE__ */ new Set();
  listaEnts.forEach((e, i) => {
    for (const k of ["entity_id", "type", "originals", "records"])
      if (!(k in e)) errs.push(`entities[${i}] sin campo '${k}'`);
    const eid = e.entity_id;
    if (vistos.has(eid)) errs.push(`entities: entity_id repetido ${String(eid ?? "None")}`);
    vistos.add(eid);
    for (const rr of lista(e.records ?? []))
      if (!ids.has(rr) && !String(rr).endsWith(":*"))
        errs.push(
          `entities[${String(eid ?? "None")}] referencia un registro inexistente: ${String(rr)}`
        );
    if (!verdadero(e.kept)) {
      const pseud = verdadero(e.pseudonyms) ? e.pseudonyms : {};
      if (!esObjeto(pseud) || Object.keys(pseud).length === 0)
        errs.push(`entities[${String(eid ?? "None")}] no conservada y sin 'pseudonyms'`);
    }
  });
  const conservados = listaConservada(listaEnts);
  const registros = todos(o).map(([, r]) => r);
  const textosRegistros = registros.map((r) => textoDeRegistro(r));
  const entero = textosRegistros.join("\n");
  for (const e of listaEnts) {
    const eid = String(e.entity_id ?? "None");
    const recs = lista(e.records ?? []).map((rr) => registro(o, rr)).filter((r) => r !== null);
    if (verdadero(e.kept)) continue;
    const pseud = verdadero(e.pseudonyms) && esObjeto(e.pseudonyms) ? e.pseudonyms : {};
    for (const [tipo, val] of Object.entries(pseud)) {
      if (typeof val !== "string" || !val) continue;
      if (recs.length && !recs.some((r) => contiene(textoDeRegistro(r), val)))
        errs.push(
          `entities[${eid}] el seud\xF3nimo ${tipo}='${val}' no aparece en ninguno de sus records`
        );
    }
    for (const orig of lista(e.originals ?? [])) {
      if (typeof orig !== "string" || Array.from(orig).length < 3) continue;
      if (estaConservada(orig, conservados)) continue;
      if (esNombreSuelto(orig)) continue;
      if (!contiene(entero, orig)) continue;
      const hits = registros.filter((_, i) => contiene(textosRegistros[i], orig)).map((r) => r.id);
      if (hits.length)
        errs.push(
          `entities[${eid}] la forma original '${orig}' sigue en la salida, p. ej. ${repr(hits.slice(0, 2))}`
        );
    }
  }
  return sinRepetir(errs);
}

// juez/validar-cli.ts
function leer(ruta) {
  return existsSync(ruta) ? readFileSync(ruta, "utf8") : null;
}
function validarDirectorio(salida, publico, maxErrores = 40) {
  const indice = JSON.parse(readFileSync(join(publico, "record_index.json"), "utf8"));
  const o = cargarSalida({
    chats: leer(join(salida, "chats.jsonl")),
    docs: leer(join(salida, "docs.jsonl")),
    tables: leer(join(salida, "tables.jsonl")),
    entities: leer(join(salida, "entities.json"))
  });
  const errores = validar(o, indice);
  if (errores.length === 0) {
    console.log("OK: la salida pasa el validador");
    return 0;
  }
  console.log(`FALLA: ${errores.length} problema(s)`);
  for (const e of errores.slice(0, maxErrores)) console.log(` - ${e}`);
  if (errores.length > maxErrores) console.log(` ... y ${errores.length - maxErrores} m\xE1s`);
  return 1;
}
function opcion(args2, nombre, defecto) {
  const i = args2.indexOf(nombre);
  return i >= 0 && args2[i + 1] ? args2[i + 1] : defecto;
}
var args = process.argv.slice(2);
if (args.length === 0 || args[0].startsWith("--")) {
  console.log("uso: node validar.mjs <output/> [--public data/public] [--max-errors 40]");
  process.exit(2);
}
process.exit(
  validarDirectorio(
    args[0],
    opcion(args, "--public", "data/public"),
    Number(opcion(args, "--max-errors", "40"))
  )
);
export {
  validarDirectorio
};
