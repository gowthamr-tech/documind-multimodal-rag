"use client";
import { useQuery } from "@tanstack/react-query";
import { Badge, Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { Page, ProcessingJob } from "@/lib/types";

export default function ProcessingPage() {
  const jobs = useQuery({ queryKey: ["processing"], queryFn: () => api<Page<ProcessingJob>>("processing/history?limit=100"), refetchInterval: 5000 });
  return (
    <>
      <PageHeader title="Processing history" subtitle="Every pipeline stage, with timings and errors (refreshes every 5s)" />
      {jobs.isLoading ? <Spinner /> : jobs.error ? <ErrorBox error={jobs.error} /> : !jobs.data?.items.length ? <Empty title="Nothing processed yet" /> : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr><th className="px-3 py-2">When</th><th className="px-3 py-2">Version</th><th className="px-3 py-2">Stage</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Duration</th><th className="px-3 py-2">Details</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {jobs.data.items.map((j) => (
                <tr key={j.id}>
                  <td className="whitespace-nowrap px-3 py-2 text-slate-500">{new Date(j.created_at).toLocaleString()}</td>
                  <td className="px-3 py-2 font-mono text-xs">{j.document_version_id.slice(0, 8)}</td>
                  <td className="px-3 py-2">{j.stage}</td>
                  <td className="px-3 py-2"><Badge tone={j.status === "succeeded" ? "ok" : j.status === "failed" ? "bad" : "neutral"}>{j.status}</Badge></td>
                  <td className="px-3 py-2">{typeof j.metrics.duration_ms === "number" ? `${(j.metrics.duration_ms / 1000).toFixed(2)}s` : "—"}</td>
                  <td className="max-w-md px-3 py-2 text-xs text-slate-500">{j.error?.message ?? Object.entries(j.metrics).filter(([k]) => k !== "duration_ms").map(([k, v]) => `${k}=${String(v)}`).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
