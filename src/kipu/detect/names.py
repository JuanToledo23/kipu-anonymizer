"""Directory-driven name matching over accent-folded text, plus kept-entity gazetteer matching."""

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from kipu.detect.mention import Mention
from kipu.directory.directory import Person
from kipu.directory.names import GIVEN, fold, split_name
from kipu.pseudo.names_co import DETECTION_ONLY_SURNAMES, SURNAMES

TOKEN = re.compile(r"[a-z0-9]+")
GAP = re.compile(r"[ \t_.\-]{1,2}|[ \t]*\n[ \t]*")
TITLE_BEFORE = re.compile(
    r"(?:\b(?:se[ñn]or|se[ñn]ora|sr|sra|srta|don|do[ñn]a|dr|dra|doctor|doctora|ing|lic|estimad[oa])\.?\s+)$",
    re.I,
)
GAZETTEER_PATH = Path(__file__).with_name("gazetteer_co.txt")
MAX_TOKENS = 4
STRONG_KINDS = ("full", "short", "handle")
PLAIN_PAIR = re.compile(r"\w+ \w+")


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    folded: str


def tokenize(text: str) -> tuple[str, list[Token]]:
    folded = fold(text)
    return folded, [Token(m.start(), m.end(), m.group(0)) for m in TOKEN.finditer(folded)]


def ngrams(
    text: str, folded: str, tokens: list[Token]
) -> Iterator[tuple[int, int, tuple[str, ...]]]:
    """Token windows (start index, end index, folded tokens) joined only by name separators."""
    for i in range(len(tokens)):
        window = [tokens[i].folded]
        yield i, i + 1, tuple(window)
        for j in range(i + 1, min(i + MAX_TOKENS, len(tokens))):
            gap = text[tokens[j - 1].end : tokens[j].start]
            if not GAP.fullmatch(gap):
                break
            window.append(tokens[j].folded)
            yield i, j + 1, tuple(window)


def is_capitalized(word: str) -> bool:
    return bool(word) and word[0].isupper()


def separator_of(span: str) -> str:
    m = re.search(r"[ \t_.\-]+", span)
    return m.group(0) if m else " "


class NameIndex:
    """Maps folded token tuples of every name variant to (person_id, kind)."""

    def __init__(self, persons: Iterable[Person]) -> None:
        self.entries: dict[tuple[str, ...], dict[str, str]] = defaultdict(dict)
        self.surnames: dict[str, set[str]] = defaultdict(set)
        self.given_lexicon = set(GIVEN)
        self.surname_lexicon = {fold(s) for s in SURNAMES + DETECTION_ONLY_SURNAMES}
        for person in persons:
            self.add(person)

    def _put(self, tokens: tuple[str, ...], person_id: str, kind: str) -> None:
        current = self.entries[tokens].get(person_id)
        order = (*STRONG_KINDS, "first")
        if current is None or order.index(kind) < order.index(current):
            self.entries[tokens][person_id] = kind

    def add(self, person: Person) -> None:
        names = set(person.names) | ({person.name.full} if person.name.full else set())
        for name in names:
            parts = split_name(name)
            given = tuple(fold(t) for t in parts.given)
            surnames = tuple(fold(t) for t in parts.surnames)
            self.given_lexicon.update(given)
            self.surname_lexicon.update(surnames)
            if not surnames:
                continue
            self._put(given + surnames, person.person_id, "full")
            self._put(given + surnames[:1], person.person_id, "short")
            if len(given) > 1:
                self._put(given[:1] + surnames[:1], person.person_id, "short")
                self._put(given[:1] + surnames, person.person_id, "full")
                self._put(given[:1], person.person_id, "first")
            self._put(given, person.person_id, "first")
            for surname in surnames:
                self.surnames[surname].add(person.person_id)
        for handle in person.handles() | person.identifiers.get("handle", set()):
            tokens = tuple(TOKEN.findall(fold(handle)))
            if len(tokens) >= 2:
                self._put(tokens, person.person_id, "handle")

    def scan(self, text: str) -> list[Mention]:
        folded, tokens = tokenize(text)
        found = []
        for i, j, window in ngrams(text, folded, tokens):
            start, end = tokens[i].start, tokens[j - 1].end
            span = text[start:end]
            hits = self.entries.get(window)
            if hits:
                found.extend(self._mentions(span, start, end, hits))
            if len(window) == 1 and window[0] in self.surnames and self._titled(text, start, span):
                pids = tuple(sorted(self.surnames[window[0]]))
                found.append(Mention(start, end, span, "NAME", kind="surname", candidates=pids))
            if len(window) >= 2 and (
                self._looks_like_person(window, span)
                or self._given_then_unknown_surname(window, span)
            ):
                found.append(Mention(start, end, span, "NAME", kind="external"))
        return found

    def _mentions(self, span: str, start: int, end: int, hits: dict[str, str]) -> list[Mention]:
        """One mention per span: every person the variant can name stays a candidate, so
        context (not the variant kind) decides between homonyms."""
        strong = {pid: kind for pid, kind in hits.items() if kind in STRONG_KINDS}
        if strong:
            kind = min(strong.values(), key=STRONG_KINDS.index)
            if kind == "handle" and " " in span:
                kind = "short"
            return [Mention(start, end, span, "NAME", kind=kind, candidates=tuple(sorted(strong)))]
        if not is_capitalized(span):
            return []
        pids = tuple(sorted(hits))
        return [Mention(start, end, span, "NAME", kind="first", candidates=pids)]

    def _titled(self, text: str, start: int, span: str) -> bool:
        return is_capitalized(span) and bool(TITLE_BEFORE.search(text[max(0, start - 14) : start]))

    def _looks_like_person(self, window: tuple[str, ...], span: str) -> bool:
        """Given name(s) followed by known surname(s), capitalized or in a filename token."""
        words = re.split(r"[\s_.\-]+", span)
        if len(words) != len(window) or len(window) > 4:
            return False
        n_given = 2 if len(window) >= 3 and window[1] in self.given_lexicon else 1
        if window[0] not in self.given_lexicon:
            return False
        if not all(w in self.surname_lexicon for w in window[n_given:]) or n_given >= len(window):
            return False
        filename_style = "_" in span and span == span.lower()
        return filename_style or all(is_capitalized(w) for w in words)

    def _given_then_unknown_surname(self, window: tuple[str, ...], span: str) -> bool:
        """`Valentina Quiceno`: a known given name followed by one unknown capitalized word."""
        words = span.split(" ")
        return (
            len(window) == 2
            and PLAIN_PAIR.fullmatch(span) is not None
            and window[0] in self.given_lexicon
            and window[1] not in self.given_lexicon
            and len(window[1]) >= 3
            and all(w[:1].isupper() and w[1:].islower() for w in words)
        )


class KeptMatcher:
    """Finds gazetteer terms; they block overlapping, weaker person candidates."""

    def __init__(self, path: Path = GAZETTEER_PATH) -> None:
        self.terms: dict[tuple[str, ...], str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if term and not term.startswith("#"):
                self.terms[tuple(TOKEN.findall(fold(term)))] = term

    def scan(self, text: str) -> list[Mention]:
        folded, tokens = tokenize(text)
        found = []
        for i, j, window in ngrams(text, folded, tokens):
            term = self.terms.get(window)
            if term is None:
                continue
            start, end = tokens[i].start, tokens[j - 1].end
            span = text[start:end]
            if len(window) == 1 and not (is_capitalized(span) and fold(span) == fold(term)):
                continue
            if len(window) == 1 and term[0].isupper() and term.isupper() and span != term:
                continue
            found.append(Mention(start, end, span, "KEPT", value=term))
        return found
