"use client";
import Link from "next/link";
import { useState } from "react";
import { SummaryBadge, VerificationBadge } from "@/components/StatusBadge";
import { Badge, cx } from "@/components/ui";
import { citationHref, parseAnswer, type Segment } from "@/lib/answer";
import type { Answer, Citation } from "@/lib/types";

function Inline({ segs, cites, onCite }: { segs: Segment[]; cites: Map<string, Citation>; onCite: (id: string) => void }) {
  return (
    <>
      {segs.map((s, i) => {
        if (s.kind === "text") return <span key={i}>{s.text}</span>;
        if (s.kind === "bold") return <strong key={i}>{s.text}</strong>;
        const c = cites.get(s.id);
        return (
          <button key={i} onClick={() => onCite(s.id)} title={c ? `${c.document_title}, p. ${c.page ?? "?"}` : "Source not verified"}
            className={cx("mx-0.5 rounded px-1 align-baseline text-xs font-medium", c ? "bg-brand-50 text-brand-700 hover:bg-brand-100" : "bg-red-50 text-bad line-through")}>
            {s.id}
          </button>
        );
      })}
    </>
  );
}

export function AnswerCard({ a }: { a: Answer }) {
  const cites = new Map(a.citations.map((c) => [c.citation_id, c]));
  const [focus, setFocus] = useState<string | null>(null);
  const blocks = parseAnswer(a.answer_markdown);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <SummaryBadge s={a.verification_summary} />
        {a.usage && <span className="text-xs text-slate-400">{(a.usage.latency_ms / 1000).toFixed(1)}s · {a.usage.prompt_tokens + a.usage.completion_tokens} tokens{a.usage.cost_usd ? ` · $${a.usage.cost_usd}` : ""}</span>}
      </div>
      <div className="prose-answer text-sm text-slate-800">
        {blocks.map((b, i) => b.kind === "p"
          ? <p key={i}><Inline segs={b.segments} cites={cites} onCite={setFocus} /></p>
          : <ul key={i}>{b.items.map((it, j) => <li key={j}><Inline segs={it} cites={cites} onCite={setFocus} /></li>)}</ul>)}
      </div>
      {Object.keys(a.computations).length > 0 && (
        <details className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
          <summary className="cursor-pointer font-medium text-slate-700">Calculations ({Object.keys(a.computations).length}) — computed in code, not by the model</summary>
          <ul className="mt-1 space-y-1">{Object.entries(a.computations).map(([k, c]) => <li key={k}><code>{k}</code>: {c.expression} = <strong>{c.display}</strong>{c.estimated && " (uses estimated chart values)"}</li>)}</ul>
        </details>
      )}
      {a.limitations.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
          <p className="font-medium">Limitations</p>
          <ul className="mt-1 list-disc pl-4">{a.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        </div>
      )}
      {a.claims.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-slate-600">Claim-by-claim verification ({a.claims.length})</summary>
          <ul className="mt-1 space-y-1">{a.claims.map((c, i) => (
            <li key={i} className="flex gap-2"><VerificationBadge v={c.status} /><span>{c.text}{c.notes.length > 0 && <span className="text-slate-500"> — {c.notes.join("; ")}</span>}</span></li>))}
          </ul>
        </details>
      )}
      {a.citations.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Sources</p>
          <ul className="space-y-2">
            {a.citations.map((c) => (
              <li key={c.citation_id} id={`cite-${c.citation_id}`}
                className={cx("rounded border bg-white p-2 text-xs", focus === c.citation_id ? "border-brand-500 ring-1 ring-brand-500" : "border-slate-200")}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="info">{c.citation_id}</Badge>
                  <Link href={citationHref(c)} className="font-medium text-brand-700 hover:underline">
                    {c.document_title} — p. {c.page ?? "n/a"}{c.page_end && c.page_end !== c.page ? `–${c.page_end}` : ""}
                  </Link>
                  <VerificationBadge v={c.verification} />
                  <span className="text-slate-400">{c.chunk_type}</span>
                </div>
                {c.section.length > 0 && <p className="mt-1 text-slate-500">{c.section.join(" › ")}</p>}
                <blockquote className="mt-1 border-l-2 border-slate-200 pl-2 text-slate-700">{c.quote}</blockquote>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
