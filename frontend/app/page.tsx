"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:9001";

type Suggestion = {
  start: number;
  end: number;
  original: string;
  suggestion: string;
  reason: string;
  source: "rule" | "llm";
  rule: string | null;
};

const SAMPLE_TEXT =
  "You can simply click the button and the user will see the kubernetes dashboard!\nThe report was written by the team.";

const SAMPLE_GUIDE = `# Sample style guide
- TERM: eBPF <- ebpf, EBPF
- TERM: Kubernetes <- kubernetes, k8s
- TERM: GitHub <- github, Github
- TERM: JavaScript <- javascript, Javascript

- Use active voice instead of passive voice.
- Address the reader as "you", never "the user".
- PATTERN: \\b(?:simply|just)\\s+ => | Avoid 'simply' and 'just'; they sound condescending
- Write in present tense, not future tense.
- PATTERN: ! => . | No exclamation marks in documentation
`;

const keyOf = (s: Suggestion) => `${s.start}-${s.end}-${s.original}`;
const dismissKey = (s: Suggestion) => `${s.rule ?? ""}|${s.original}|${s.suggestion}`;

export default function Page() {
  const [text, setText] = useState(SAMPLE_TEXT);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [status, setStatus] = useState<"idle" | "checking" | "error">("idle");
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<string | null>(null);
  const [guide, setGuide] = useState(SAMPLE_GUIDE);
  const [guideMsg, setGuideMsg] = useState("");
  const [guideVersion, setGuideVersion] = useState(0);
  const mirror = useRef<HTMLDivElement>(null);

  // Check the text 700ms after the last keystroke; cancel stale requests.
  useEffect(() => {
    if (!text.trim()) return;
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      setStatus("checking");
      try {
        const res = await fetch(`${API}/check`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
          signal: ctrl.signal,
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        setSuggestions((await res.json()).suggestions);
        setStatus("idle");
      } catch (e) {
        if ((e as Error).name !== "AbortError") setStatus("error");
      }
    }, 700);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [text, guideVersion]);

  // Hide suggestions that no longer match the text (user kept typing) or were dismissed.
  const visible = useMemo(
    () =>
      suggestions
        .filter((s) => text.slice(s.start, s.end) === s.original && !dismissed.has(dismissKey(s)))
        .sort((a, b) => a.start - b.start),
    [suggestions, text, dismissed],
  );

  const segments = useMemo(() => {
    const out: { text: string; s?: Suggestion }[] = [];
    let pos = 0;
    for (const s of visible) {
      if (s.start < pos) continue;
      if (s.start > pos) out.push({ text: text.slice(pos, s.start) });
      out.push({ text: text.slice(s.start, s.end), s });
      pos = s.end;
    }
    out.push({ text: text.slice(pos) });
    return out;
  }, [text, visible]);

  useEffect(() => {
    if (active) document.getElementById(`card-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function accept(s: Suggestion) {
    const delta = s.suggestion.length - (s.end - s.start);
    setText(text.slice(0, s.start) + s.suggestion + text.slice(s.end));
    setSuggestions((prev) =>
      prev
        .filter((x) => x !== s)
        .map((x) => (x.start >= s.end ? { ...x, start: x.start + delta, end: x.end + delta } : x)),
    );
  }

  function dismiss(s: Suggestion) {
    setDismissed((prev) => new Set(prev).add(dismissKey(s)));
  }

  function pickAtCaret(el: HTMLTextAreaElement) {
    const c = el.selectionStart;
    const hit = visible.find((s) => c >= s.start && c <= s.end);
    setActive(hit ? keyOf(hit) : null);
  }

  async function applyGuide() {
    setGuideMsg("Applying…");
    try {
      const res = await fetch(`${API}/styleguide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: guide }),
      });
      if (!res.ok) throw new Error();
      const d = await res.json();
      setGuideMsg(`Loaded ${d.rules} rules: ${d.deterministic_rules} exact, ${d.rules - d.deterministic_rules} checked by AI.`);
      setDismissed(new Set());
      setGuideVersion((v) => v + 1);
    } catch {
      setGuideMsg("Couldn't load the guide. Check that the backend is running.");
    }
  }

  const shown = text.trim() ? status : "idle"; // empty text: nothing to check

  const label = (s: Suggestion) =>
    s.source === "rule" ? `Style guide ${s.rule}` : s.rule ? `Style guide ${s.rule} (AI)` : "Grammar";

  return (
    <main className="page">
      <header className="top">
        <div>
          <h1>Style Checker</h1>
          <p className="muted">Checks grammar and your style guide as you type.</p>
        </div>
        <p className={`status ${shown}`} role="status">
          {shown === "checking" && "Checking…"}
          {shown === "error" && `Can't reach the checker at ${API}. Start the backend, then edit the text.`}
          {shown === "idle" && `${visible.length} suggestion${visible.length === 1 ? "" : "s"}`}
        </p>
      </header>

      <details className="guide">
        <summary>Style guide</summary>
        <p className="muted small">
          One rule per line. <code>TERM: eBPF &lt;- ebpf</code> and <code>PATTERN: regex =&gt; replacement | reason</code> are
          exact; other lines are checked by AI.
        </p>
        <textarea value={guide} onChange={(e) => setGuide(e.target.value)} rows={9} spellCheck={false} aria-label="Style guide text" />
        <div className="row">
          <button onClick={applyGuide}>Apply guide</button>
          <label className="file">
            Load from file
            <input type="file" accept=".md,.txt" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setGuide(await f.text()); }} />
          </label>
          <span className="muted small" role="status">{guideMsg}</span>
        </div>
      </details>

      <section className="workspace">
        <div className="editor">
          <div className="mirror" ref={mirror} aria-hidden>
            {segments.map((seg, i) =>
              seg.s ? (
                <mark key={i} className={`${seg.s.source === "rule" ? "m-rule" : "m-model"} ${keyOf(seg.s) === active ? "on" : ""}`}>
                  {seg.text}
                </mark>
              ) : (
                <span key={i}>{seg.text}</span>
              ),
            )}
            {"\u200b"}
          </div>
          <textarea
            className="input"
            value={text}
            spellCheck={false}
            aria-label="Text to check"
            placeholder="Paste or type text to check."
            onChange={(e) => setText(e.target.value)}
            onScroll={(e) => { if (mirror.current) mirror.current.scrollTop = e.currentTarget.scrollTop; }}
            onClick={(e) => pickAtCaret(e.currentTarget)}
            onKeyUp={(e) => pickAtCaret(e.currentTarget)}
          />
        </div>

        <aside className="notes" aria-label="Suggestions">
          {visible.length === 0 && (
            <p className="empty">{text.trim() ? "No suggestions." : "Paste or type text to check it."}</p>
          )}
          {visible.map((s) => (
            <article key={keyOf(s)} id={`card-${keyOf(s)}`} className={`card ${keyOf(s) === active ? "on" : ""}`} onClick={() => setActive(keyOf(s))}>
              <p className={`tag ${s.source === "rule" ? "t-rule" : "t-model"}`}>{label(s)}</p>
              <p className="change">
                {s.suggestion === "" ? (
                  <>Delete <del>{s.original.trim()}</del></>
                ) : (
                  <><del>{s.original}</del> <span aria-hidden>to</span> <ins>{s.suggestion}</ins></>
                )}
              </p>
              <p className="muted small">{s.reason}</p>
              <div className="row">
                <button onClick={(e) => { e.stopPropagation(); accept(s); }}>Accept</button>
                <button className="ghost" onClick={(e) => { e.stopPropagation(); dismiss(s); }}>Dismiss</button>
              </div>
            </article>
          ))}
        </aside>
      </section>
    </main>
  );
}
