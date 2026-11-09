import re
from dataclasses import dataclass, field

# Deterministic rule syntax in the style guide (one per line):
#   TERM: eBPF <- ebpf, EBPF                      exact terminology
#   PATTERN: <regex> => <replacement> | <reason>   regex rewrite (empty replacement = delete)
# Every other non-heading line is a free-text rule handled by RAG + LLM.
TERM_RE = re.compile(r"^TERM:\s*(?P<canon>.+?)\s*<-\s*(?P<vars>.+)$")
PATTERN_RE = re.compile(r"^PATTERN:\s*(?P<pat>.+?)\s*=>\s*(?P<rep>[^|]*?)\s*(?:\|\s*(?P<why>.+))?$")


@dataclass
class Rule:
    id: str
    text: str
    canonical: str | None = None
    variants: list[str] = field(default_factory=list)
    pattern: str | None = None
    replacement: str = ""

    @property
    def deterministic(self) -> bool:
        return bool(self.canonical or self.pattern)


def parse_guide(text: str) -> list[Rule]:
    rules = []
    for line in text.splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if not line or line.startswith("#"):
            continue
        rid = f"R{len(rules) + 1}"
        if m := TERM_RE.match(line):
            canon = m["canon"].strip()
            variants = [v.strip() for v in m["vars"].split(",") if v.strip()]
            rules.append(Rule(rid, f"Always write '{canon}'.", canon, variants))
        elif m := PATTERN_RE.match(line):
            rep = m["rep"].strip()
            why = m["why"] or "Style guide rule"
            rules.append(Rule(rid, why.strip(), pattern=m["pat"], replacement=rep))
        else:
            rules.append(Rule(rid, line))
    return rules


def find_violations(paragraph: str, rules: list[Rule]):
    """Deterministic pass: returns (start, end, found, replacement, rule)."""
    hits = []
    for r in rules:
        if r.canonical:
            forms = sorted(set(r.variants) | {r.canonical}, key=len, reverse=True)
            rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(f) for f in forms) + r")(?!\w)", re.I)
            for m in rx.finditer(paragraph):
                if m.group(1) != r.canonical:
                    hits.append((m.start(1), m.end(1), m.group(1), r.canonical, r))
        elif r.pattern:
            for m in re.finditer(r.pattern, paragraph, re.I):
                if m.end() > m.start():
                    hits.append((m.start(), m.end(), m.group(0), m.expand(r.replacement), r))
    return hits