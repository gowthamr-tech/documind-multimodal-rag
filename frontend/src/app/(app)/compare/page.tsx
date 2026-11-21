"use client";
import { useState, type FormEvent } from "react";
import { Button, Card, Empty, PageHeader } from "@/components/ui";
import { Conversation } from "@/features/Conversation";
import { DocPicker } from "@/features/DocPicker";
import { useAsk } from "@/hooks/useAsk";

export default function ComparePage() {
  const [docs, setDocs] = useState<string[]>([]);
  const [q, setQ] = useState("Compare revenue growth across these reports, identify the largest expense category in each, and explain the differences.");
  const { turns, busy, ask, reset } = useAsk("query/compare");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (docs.length >= 2 && q.trim()) void ask(q.trim(), docs);
  }

  return (
    <>
      <PageHeader title="Document comparison" subtitle="Evidence is balanced across every selected document, and each one is cited."
        actions={turns.length > 0 && <Button variant="secondary" onClick={reset}>Clear</Button>} />
      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        <Card><DocPicker selected={docs} onChange={setDocs} min={2} /></Card>
        <div className="space-y-4">
          <form onSubmit={submit} className="space-y-2">
            <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={3} aria-label="Comparison question"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm" />
            <Button type="submit" disabled={busy || docs.length < 2}>{docs.length < 2 ? "Select at least two documents" : busy ? "Comparing…" : "Compare"}</Button>
          </form>
          {turns.length ? <Conversation turns={turns} busy={busy} /> : <Empty title="Pick two or more documents and ask a comparison question" />}
        </div>
      </div>
    </>
  );
}
