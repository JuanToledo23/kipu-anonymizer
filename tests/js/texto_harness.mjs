// Evaluates the juez/texto.ts section of validar.mjs verbatim and runs cases read from stdin.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const src = readFileSync(join(root, "validar.mjs"), "utf8");
const start = src.indexOf("// juez/texto.ts");
const end = src.indexOf("// juez/esquema.ts");
const api = new Function(`${src.slice(start, end)}\nreturn { norm, digitosCompactos, contiene };`)();

const cases = JSON.parse(readFileSync(0, "utf8"));
const out = cases.map(([fn, ...args]) => api[fn](...args));
process.stdout.write(JSON.stringify(out));
