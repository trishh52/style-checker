import json
import os
import re
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel

from retriever import Retriever
from rules import find_violations, parse_guide

load_dotenv()

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
client = Groq()  # reads GROQ_API_KEY

app = FastAPI(title="Grammar Checker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_PROMPT = """You are a careful grammar, spelling and style checker.
Find errors in the user's paragraph and return ONLY a JSON object:
{"suggestions": [{"original": "...", "suggestion": "...", "reason": "...", "rule": "R3 or null"}]}

Rules:
- Make the smallest edit that fixes the problem. For voice or tense rules, "original" may be the whole clause, rewritten so it keeps the same meaning (e.g. "The report was written by the team" -> "The team wrote the report").
- The fix must keep the original meaning and who does what. If you cannot fix it without changing the meaning, skip it.
- "original" must be an exact, contiguous substring of the paragraph.
- Keep "original" short (the word or phrase that needs to change).
- "reason" is under 12 words.
- Do not change names, code, URLs, or technical terms unless clearly misspelled.
- If there are no problems, return {"suggestions": []}."""

RULES_PROMPT = """
Style rules. Flag a violation ONLY if the paragraph clearly breaks one, and put
that rule's id in "rule". Ignore rules that do not apply:
{rules}"""

# --- style guide state -------------------------------------------------------
STATE = {"rules": [], "version": 0, "retriever": Retriever()}


def load_guide(text: str) -> dict:
    rules = parse_guide(text)
    STATE["rules"] = rules
    STATE["retriever"].index([r for r in rules if not r.deterministic])
    STATE["version"] += 1  # invalidates the paragraph cache
    terms = sum(1 for r in rules if r.deterministic)
    return {"rules": len(rules), "deterministic_rules": terms, "retrieval": STATE["retriever"].mode}


_default = Path(__file__).with_name("styleguide.md")
if _default.exists():
    load_guide(_default.read_text(encoding="utf-8"))


# --- models ------------------------------------------------------------------
class CheckRequest(BaseModel):
    text: str


class GuideRequest(BaseModel):
    text: str


class Suggestion(BaseModel):
    start: int
    end: int
    original: str
    suggestion: str
    reason: str
    source: str  # "rule" (deterministic) or "llm"
    rule: str | None = None


# --- core --------------------------------------------------------------------
def split_paragraphs(text: str):
    for m in re.finditer(r"[^\n]+", text):
        if m.group().strip():
            yield m.start(), m.group()


@lru_cache(maxsize=512)
def llm_check(paragraph: str, version: int) -> tuple:
    """LLM pass with retrieved style rules. `version` busts the cache on new guides."""
    system = BASE_PROMPT
    free = STATE["rules"]
    retrieved = free if len(free) <= 12 else STATE["retriever"].search(paragraph, k=4)
    if retrieved:
        system += RULES_PROMPT.format(rules="\n".join(f"[{r.id}] {r.text}" for r in retrieved))
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": paragraph}],
    )
    try:
        items = json.loads(resp.choices[0].message.content).get("suggestions", [])
    except (json.JSONDecodeError, AttributeError):
        return ()

    results, used = [], []
    for item in items:
        orig, sugg = str(item.get("original", "")), str(item.get("suggestion", ""))
        if not orig or orig == sugg:
            continue
        pos = paragraph.find(orig)
        while pos != -1 and any(pos < e and pos + len(orig) > s for s, e in used):
            pos = paragraph.find(orig, pos + 1)
        if pos == -1:
            continue
        used.append((pos, pos + len(orig)))
        rule = item.get("rule")
        results.append((pos, orig, sugg, str(item.get("reason", "")), str(rule) if rule else None))
    return tuple(results)


@app.post("/check")
def check(req: CheckRequest) -> dict:
    out = []
    for offset, para in split_paragraphs(req.text):
        # 1) deterministic terminology pass (no LLM, always exact)
        taken = []
        for s, e, found, repl, rule in find_violations(para, STATE["rules"]):
            taken.append((s, e))
            out.append(Suggestion(
                start=offset + s, end=offset + e, original=found, suggestion=repl,
                reason=rule.text, source="rule", rule=rule.id,
            ))
        # 2) LLM pass, skipping anything the rule layer already covers
        for pos, orig, sugg, reason, rule_id in llm_check(para, STATE["version"]):
            if any(pos < e and pos + len(orig) > s for s, e in taken):
                continue
            out.append(Suggestion(
                start=offset + pos, end=offset + pos + len(orig), original=orig,
                suggestion=sugg, reason=reason, source="llm", rule=rule_id,
            ))
    out.sort(key=lambda s: s.start)
    return {"suggestions": [s.model_dump() for s in out]}


@app.post("/styleguide")
def set_guide(req: GuideRequest) -> dict:
    return load_guide(req.text)


@app.get("/styleguide")
def get_guide() -> dict:
    return {"version": STATE["version"], "rules": [r.__dict__ for r in STATE["rules"]]}


@app.get("/health")
def health():
    return {"ok": True, "retrieval": STATE["retriever"].mode}