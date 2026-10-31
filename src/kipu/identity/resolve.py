"""Union-find identity resolution over person seeds, with cannot-link constraints."""

import logging
import re

from kipu.directory.names import fold, split_name
from kipu.directory.seed import PersonSeed

log = logging.getLogger(__name__)

EXCLUSIVE_KINDS = ("client", "employee", "client_user", "employee_user")


class UnionFind:
    def __init__(self, seeds: list[PersonSeed]) -> None:
        self.parent = list(range(len(seeds)))
        self.members: dict[int, list[int]] = {i: [i] for i in range(len(seeds))}
        self.seeds = seeds

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def cannot_link(self, a: int, b: int) -> bool:
        """Two clusters cannot merge if they hold different cédulas or two rows of one master."""
        ma, mb = self.members[a], self.members[b]
        ced_a = {v for i in ma for t, v in self.seeds[i].norm_ids(("cedula",))}
        ced_b = {v for i in mb for t, v in self.seeds[i].norm_ids(("cedula",))}
        if ced_a and ced_b and not ced_a & ced_b:
            return True
        for kind in EXCLUSIVE_KINDS:
            if any(self.seeds[i].kind == kind for i in ma) and any(
                self.seeds[i].kind == kind for i in mb
            ):
                return True
        return False

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if self.cannot_link(ra, rb):
            return False
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.members[ra].extend(self.members.pop(rb))
        return True

    def clusters(self) -> list[list[PersonSeed]]:
        return [[self.seeds[i] for i in sorted(m)] for _, m in sorted(self.members.items())]


def resolve(
    seeds: list[PersonSeed], texts_by_uid: dict[str, str] | None = None
) -> list[list[PersonSeed]]:
    """Cluster seeds into persons: strong keys first, then unique name joins for chat users."""
    uf = UnionFind(seeds)
    by_key: dict[tuple[str, str], int] = {}
    for i, seed in enumerate(seeds):
        for key in sorted(seed.norm_ids()):
            if key in by_key:
                if not uf.union(by_key[key], i):
                    log.warning("cannot-link prevented merge on %s identifier", key[0])
            else:
                by_key[key] = i
    _join_users_by_name(uf, texts_by_uid or {})
    return uf.clusters()


def _name_keys(name: str) -> set[str]:
    parts = split_name(name)
    return {fold(parts.full), fold(parts.short)}


def _join_users_by_name(uf: UnionFind, texts_by_uid: dict[str, str]) -> None:
    """Repeat until stable: linking one homonym can leave a single candidate for the other."""
    while _join_pass(uf, texts_by_uid):
        pass


def _join_pass(uf: UnionFind, texts_by_uid: dict[str, str]) -> bool:
    targets = {"client_user": "client", "employee_user": "employee"}
    changed = False
    for i, seed in enumerate(uf.seeds):
        target_kind = targets.get(seed.kind)
        if not target_kind or _cluster_has(uf, i, target_kind):
            continue
        candidates = [
            j
            for j, other in enumerate(uf.seeds)
            if other.kind == target_kind
            and fold(seed.full_name) in _name_keys(other.full_name)
            and not _cluster_has(uf, j, seed.kind)
        ]
        if len(candidates) > 1:
            candidates = _disambiguate(uf, seed, candidates, texts_by_uid)
        if len(candidates) == 1 and uf.union(candidates[0], i):
            changed = True
    return changed


def _cluster_has(uf: UnionFind, i: int, kind: str) -> bool:
    return any(uf.seeds[j].kind == kind for j in uf.members[uf.find(i)])


def _disambiguate(
    uf: UnionFind, user: PersonSeed, candidates: list[int], texts_by_uid: dict[str, str]
) -> list[int]:
    """Prefer the candidate whose email local part is the Slack username, then text evidence."""
    handles = {h.lower() for h in user.identifiers.get("handle", set())}
    by_handle = [
        j
        for j in candidates
        if handles & {v.split("@")[0] for _, v in uf.seeds[j].norm_ids(("email",))}
    ]
    if len(by_handle) == 1:
        return by_handle
    uid = next(iter(user.identifiers.get("uid", {""})))
    text = texts_by_uid.get(uid, "")
    if not text:
        return candidates
    digits = re.sub(r"(?<=\d)[ .\-](?=\d)", "", text)
    lowered = text.lower()
    hits = []
    for j in candidates:
        ids = uf.seeds[j].norm_ids(("cedula", "phone", "email", "cliente_id"))
        if any((v in digits if t in ("cedula", "phone") else v.lower() in lowered) for t, v in ids):
            hits.append(j)
    return hits or candidates
