from dataclasses import dataclass, field, replace

# Labels that carry a person identifier and get a surrogate.
IDENTIFIER_LABELS = ("EMAIL", "CEDULA", "PHONE", "PLATE", "PASSPORT", "BIRTH", "ADDRESS")

# Overlap resolution: longer span wins, then lower number here.
PRIORITY = {
    "EMAIL": 0,
    "CEDULA": 1,
    "PHONE": 1,
    "PASSPORT": 1,
    "PLATE": 1,
    "BIRTH": 1,
    "ADDRESS": 1,
    "SLACK": 1,
    "NAME": 2,
    "KEPT": 3,
    "QUASI_BARRIO": 2.5,
    "QUASI_OCCUPATION": 4,
    "QUASI_YEAR": 4,
}
NAME_KIND_PRIORITY = {"full": 0, "short": 1, "external": 2, "handle": 2, "first": 3, "surname": 4}


@dataclass(frozen=True)
class Mention:
    """A span of a text surface that refers to a person, an identifier or a kept entity.

    `source` names the detector family ("rules" today); an LLM layer adds candidates with
    source="llm" and they go through the same overlap resolution.
    """

    start: int
    end: int
    text: str
    label: str
    person_id: str | None = None
    source: str = "rules"
    kind: str = ""
    value: str = ""
    candidates: tuple[str, ...] = field(default=(), compare=False)

    def with_person(self, person_id: str | None) -> "Mention":
        return replace(self, person_id=person_id)

    def rank(self) -> tuple[int, float, int]:
        priority: float = PRIORITY.get(self.label, 9)
        name_rank = 0
        if self.label == "NAME":
            name_rank = NAME_KIND_PRIORITY.get(self.kind, 5)
            if self.kind == "external":
                priority = PRIORITY["KEPT"] + 0.5
        return (-(self.end - self.start), priority, name_rank)


def resolve_overlaps(mentions: list[Mention]) -> list[Mention]:
    """Keep a non-overlapping subset: longest span first, then label priority."""
    chosen: list[Mention] = []
    taken: list[tuple[int, int]] = []
    for m in sorted(mentions, key=lambda m: (m.rank(), m.start)):
        if any(m.start < end and start < m.end for start, end in taken):
            continue
        chosen.append(m)
        taken.append((m.start, m.end))
    return sorted(chosen, key=lambda m: m.start)
