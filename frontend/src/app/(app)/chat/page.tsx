"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Button, Card, Empty, PageHeader, Spinner } from "@/components/ui";
import { Conversation } from "@/features/Conversation";
import { DocPicker } from "@/features/DocPicker";
import { useAsk } from "@/hooks/useAsk";

function Chat() {
  const initial = useSearchParams().get("doc");
  const [docs, setDocs] = useState<string[]>(initial ? [initial] : []);
  const [q, setQ] = useState("");
  const { turns, busy, ask, reset, stop } = useAsk("query");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    void ask(q.trim(), docs);
    setQ("");
  }

  return (
    <>
      <PageHeader title="AI chat" subtitle="Answers cite the exact page they come from; every claim is checked before it is shown."
        actions={turns.length > 0 && <Button variant="secondary" onClick={reset}>New conversation</Button>} />
      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        <Card><DocPicker selected={docs} onChange={setDocs} /></Card>
        <div className="flex min-h-[60vh] flex-col gap-4">
          <div className="flex-1">
            {turns.length ? <Conversation turns={turns} busy={busy} /> : (
              <Empty title="Ask a question about your documents">e.g. “Which expense category was largest, and by how much did it exceed cost of revenue?”</Empty>
            )}
          </div>
          <form onSubmit={submit} className="sticky bottom-0 flex gap-2 bg-slate-50 py-2">
            <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={2} placeholder={turns.length ? "Ask a follow-up…" : "Ask a question…"}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) submit(e); }} aria-label="Question"
              className="flex-1 resize-none rounded-md border border-slate-300 bg-white px-3 py-2 text-sm" />
            {busy ? <Button type="button" variant="secondary" onClick={stop}>Stop</Button> : <Button type="submit" disabled={!q.trim()}>Ask</Button>}
          </form>
        </div>
      </div>
    </>
  );
}

export default function ChatPage() {
  return <Suspense fallback={<Spinner />}><Chat /></Suspense>;
}
