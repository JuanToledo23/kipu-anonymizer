"""Person seeds from the master tables and users.json, with normalized strong identifiers."""

import re
from dataclasses import dataclass, field
from typing import Any

from kipu.directory.names import fold
from kipu.ingest.records import TableRecord

STRONG = ("cedula", "email", "phone", "uid", "cliente_id", "kp_id")


def norm_identifier(id_type: str, value: str) -> str:
    value = value.strip()
    if id_type in ("cedula", "phone"):
        digits = re.sub(r"\D", "", value)
        if id_type == "phone" and len(digits) == 12 and digits.startswith("57"):
            digits = digits[2:]
        return digits
    if id_type in ("email", "handle"):
        return value.lower()
    if id_type in ("plate", "passport", "cliente_id", "kp_id", "uid", "radicado"):
        return re.sub(r"[\s-]", "", value).upper()
    return " ".join(fold(value).split())


@dataclass
class PersonSeed:
    key: str
    kind: str
    full_name: str
    gender_hint: str = ""
    identifiers: dict[str, set[str]] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)

    def add(self, id_type: str, raw: str) -> None:
        if raw and raw.strip():
            self.identifiers.setdefault(id_type, set()).add(raw.strip())

    def norm_ids(self, types: tuple[str, ...] = STRONG) -> set[tuple[str, str]]:
        return {
            (t, norm_identifier(t, v))
            for t, values in self.identifiers.items()
            if t in types
            for v in values
            if norm_identifier(t, v)
        }


def seeds_from_tables(tables: list[TableRecord]) -> list[PersonSeed]:
    seeds = []
    for record in tables:
        c = record.columns
        if record.table == "clientes":
            seed = PersonSeed(
                record.id, "client", c.get("nombre_completo", ""), c.get("ocupacion", "")
            )
            seed.add("cliente_id", c.get("cliente_id", ""))
            seed.add("cedula", c.get("num_doc", ""))
            seed.add("email", c.get("correo", ""))
            seed.add("phone", c.get("celular", ""))
            seed.add("address", c.get("direccion", ""))
            seed.add("birth_iso", c.get("fecha_nacimiento", ""))
            seed.attrs = {
                k: c.get(src, "")
                for k, src in (
                    ("ciudad", "ciudad"),
                    ("barrio", "barrio"),
                    ("ocupacion", "ocupacion"),
                    ("vinculacion", "fecha_vinculacion"),
                )
            }
            seeds.append(seed)
        elif record.table == "empleados":
            seed = PersonSeed(record.id, "employee", c.get("nombre", ""), c.get("cargo", ""))
            seed.add("kp_id", c.get("id", ""))
            seed.add("cedula", c.get("documento", ""))
            seed.add("email", c.get("correo", ""))
            seed.add("phone", c.get("celular", ""))
            seed.add("birth_iso", c.get("fecha_nacimiento", ""))
            seed.attrs = {"ciudad": c.get("ciudad", ""), "reporta_a": c.get("reporta_a", "")}
            seeds.append(seed)
        elif record.table == "pqrs":
            seed = PersonSeed(record.id, "pqr_link", "")
            seed.add("radicado", c.get("radicado", ""))
            seed.add("cliente_id", c.get("cliente_id", ""))
            seed.add("cedula", c.get("cedula", ""))
            seeds.append(seed)
    return seeds


def seeds_from_users(users: list[dict[str, Any]]) -> list[PersonSeed]:
    seeds = []
    for user in users:
        profile = user.get("profile", {})
        name = user.get("real_name") or profile.get("real_name") or ""
        if user.get("is_bot") or user.get("deleted") or not name:
            continue
        title = profile.get("title", "")
        kind = "client_user" if title == "Cliente" else "employee_user"
        seed = PersonSeed(f"users:{user['id']}", kind, name, title)
        seed.add("uid", user["id"])
        seed.add("handle", user.get("name", ""))
        seed.add("email", profile.get("email", ""))
        seed.add("phone", profile.get("phone", ""))
        seeds.append(seed)
    return seeds
