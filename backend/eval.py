"""Evaluate rule adherence. Run:  python eval.py   (add 'regen' to rebuild the test set)

Conditions compared:
  A. LLM only, no style rules in the prompt
  B. LLM only, ALL rules pasted into the prompt (no code layer)
  C. Hybrid (what the API does): deterministic rule layer + LLM with all rules
A violation counts as caught if any suggestion overlaps the violating phrase.
API results are cached in eval_cache.json, so re-runs only pay for what's new.
"""
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from main import BASE_PROMPT, MODEL, RULES_PROMPT, STATE, client
from rules import find_violations

DATA, CACHE, N = Path("eval_data.json"), Path("eval_cache.json"), 5
ERRORS: list[str] = []
_cache: dict = json.loads(CACHE.read_text()) if CACHE.exists() else {}


def chat_json(system: str, user: str, temp: float = 0.0):
    """Returns parsed JSON, or None if every retry failed (recorded in ERRORS)."""
    err = None
    for i in range(5):
        try:
            r = client.chat.completions.create(
                model=MODEL, temperature=temp, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return json.loads(r.choices[0].message.content)
        except Exception as e:  # rate limit, bad JSON, network...
            err = e
            time.sleep(2 ** (i + 1))
    ERRORS.append(repr(err)[:150])
    return None


def generate() -> dict:
    rules, data = STATE["rules"], {"violations": [], "clean": []}
    sys_p = "You write test data for a style-guide checker. Return JSON only."
    for r in rules:
        out = chat_json(sys_p, (
            f'Style rule: "{r.text}"\nWrite {N} different short documentation-style sentences that '
            'clearly VIOLATE this rule, each in a different way. For each give "violating", '
            '"phrase" (exact substring of the sentence that violates it) and "clean" (minimal fix). '
            'Return {"items": [...]}'), 0.7) or {}
        for it in out.get("items", []):
            if not isinstance(it, dict):
                continue
            v, p = it.get("violating"), it.get("phrase")
            if isinstance(v, str) and isinstance(p, str) and p and p in v:
                data["violations"].append({"rule": r.id, "exact": r.deterministic, "violating": v, "phrase": p})
        print(f"generated {r.id}", flush=True)
    allr = "\n".join(f"- {r.text}" for r in rules)
    out = chat_json(sys_p, f"Write 20 short documentation sentences that follow ALL these rules:\n{allr}\n"
                           'Return {"sentences": [...]}', 0.7) or {}
    data["clean"] = [x for x in out.get("sentences", []) if isinstance(x, str)]
    DATA.write_text(json.dumps(data, indent=1))
    return data


def llm_spans(text: str, rules) -> list[tuple[int, int]]:
    system = BASE_PROMPT
    if rules:
        system += RULES_PROMPT.format(rules="\n".join(f"[{r.id}] {r.text}" for r in rules))
    key = hashlib.md5(f"{MODEL}|{system}|{text}".encode()).hexdigest()
    if key in _cache:
        return [tuple(s) for s in _cache[key]]
    out = chat_json(system, text)
    if out is None:
        return []  # failed call: counted in ERRORS, never cached
    spans = []
    for it in out.get("suggestions", []):
        if not isinstance(it, dict):
            continue
        o = str(it.get("original", ""))
        if o and (i := text.find(o)) != -1 and o != it.get("suggestion"):
            spans.append((i, i + len(o)))
    _cache[key] = spans
    return spans


def code_spans(text: str) -> list[tuple[int, int]]:
    return [(s, e) for s, e, *_ in find_violations(text, STATE["rules"])]


def hit(spans, text, phrase) -> bool:
    a = text.find(phrase)
    return any(s < a + len(phrase) and e > a for s, e in spans)


def main_eval():
    data = json.loads(DATA.read_text()) if DATA.exists() and "regen" not in sys.argv else generate()
    texts = list(dict.fromkeys([v["violating"] for v in data["violations"]] + data["clean"]))
    print(f"{len(texts)} sentences, 2 LLM calls each (cached ones are free)", flush=True)

    def work(t):
        return t, llm_spans(t, []), llm_spans(t, STATE["rules"])

    res = {}
    with ThreadPoolExecutor(1) as ex:
        for n, (t, a, b) in enumerate(ex.map(work, texts), 1):
            res[t] = (a, b)
            if n % 10 == 0 or n == len(texts):
                print(f"  {n}/{len(texts)}", flush=True)
    CACHE.write_text(json.dumps(_cache))

    conds = {"A  LLM, no rules": lambda t: res[t][0],
             "B  LLM, all rules in prompt": lambda t: res[t][1],
             "C  Hybrid (rule layer + LLM)": lambda t: code_spans(t) + res[t][1]}
    rows = {}
    for name, fn in conds.items():
        caught = {True: [0, 0], False: [0, 0]}  # exact? -> [hits, total]
        for v in data["violations"]:
            ok = hit(fn(v["violating"]), v["violating"], v["phrase"])
            caught[v["exact"]][1] += 1
            caught[v["exact"]][0] += ok
            if not ok and name.startswith("C"):
                print(f'  miss [{v["rule"]}]: {v["violating"]!r}  (phrase: {v["phrase"]!r})')
        fp = sum(bool(fn(s)) for s in data["clean"])
        rows[name] = (caught[False], caught[True], [fp, len(data["clean"])])
    pct = lambda x: f"{100 * x[0] / max(x[1], 1):.0f}% ({x[0]}/{x[1]})"
    print("\n| Condition | Fuzzy rules caught | Exact rules caught | False positives |\n|---|---|---|---|")
    for name, (f, e, p) in rows.items():
        print(f"| {name} | {pct(f)} | {pct(e)} | {pct(p)} |")
    Path("eval_results.json").write_text(json.dumps(rows, indent=1))
    if ERRORS:
        print(f"\nWARNING: {len(ERRORS)} API calls failed, so those sentences count as misses. "
              f"Last error: {ERRORS[-1]}\nRe-run `python eval.py`; successful results are cached.")


if __name__ == "__main__":
    main_eval()