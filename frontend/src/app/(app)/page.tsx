"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { SummaryBadge } from "@/components/StatusBadge";
import { Card, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { useDocuments } from "@/hooks/queries";
import { api } from "@/lib/api";
import type { Summary } from "@/lib/types";

interface Run { id: string; question: string; status: string; verification_summary: Summary | null; latency_ms: number | null; created_at: string; prompt_tokens: number; completion_tokens: number }

function Stat({ label, value, href }: { label: string; value: number | string; href?: string }) {
  const body = (<><p className="text-xs uppercase tracking-wide text-slate-500">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p></>);
  return <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">{href ? <Link href={href}>{body}</Link> : body}</div>;
}

export default function Dashboard() {
  const docs = useDocuments();
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<Run[]>("query/runs?limit=20") });
  const items = docs.data?.items ?? [];
  const by = (s: string) => items.filter((d) => d.current_version?.status === s).length;
  const verified = (runs.data ?? []).filter((r) => r.verification_summary === "verified").length;

  return (
    <>
      <PageHeader title="Dashboard" subtitle="Your document workspace at a glance" />
      {docs.error ? <ErrorBox error={docs.error} onRetry={() => docs.refetch()} /> : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Documents" value={docs.isLoading ? "…" : items.length} href="/documents" />
          <Stat label="Ready" value={by("ready")} />
          <Stat label="Processing" value={by("processing") + by("queued")} href="/processing" />
          <Stat label="Failed" value={by("failed")} href="/processing" />
          <Stat label="Recent answers verified" value={runs.data ? `${verified}/${runs.data.length}` : "…"} />
        </div>
      )}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Card title="Recent questions" actions={<Link className="text-sm text-brand-700 hover:underline" href="/chat">Open chat</Link>}>
          {runs.isLoading ? <Spinner /> : runs.error ? <ErrorBox error={runs.error} /> : !runs.data?.length ? (
            <p className="text-sm text-slate-500">No questions yet.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {runs.data.map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <span className="truncate">{r.question}</span>
                  <span className="flex shrink-0 items-center gap-2">
                    {r.verification_summary ? <SummaryBadge s={r.verification_summary} /> : <span className="text-xs text-slate-500">{r.status}</span>}
                    {r.latency_ms != null && <span className="text-xs text-slate-400">{(r.latency_ms / 1000).toFixed(1)}s</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recently added" actions={<Link className="text-sm text-brand-700 hover:underline" href="/documents/upload">Upload</Link>}>
          {docs.isLoading ? <Spinner /> : !items.length ? <p className="text-sm text-slate-500">No documents yet.</p> : (
            <ul className="divide-y divide-slate-100">
              {items.slice(0, 8).map((d) => (
                <li key={d.id} className="py-2 text-sm"><Link href={`/documents/${d.id}`} className="hover:text-brand-700">{d.title}</Link></li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
