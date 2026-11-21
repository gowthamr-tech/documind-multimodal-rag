"use client";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DocStatus } from "@/components/StatusBadge";
import { Badge, Button, Empty, ErrorBox, Input, PageHeader, Spinner } from "@/components/ui";
import { useDocuments, useMe } from "@/hooks/queries";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/table";

export default function DocumentLibrary() {
  const [q, setQ] = useState("");
  const docs = useDocuments(q ? { q } : {});
  const me = useMe();
  const qc = useQueryClient();
  const canDelete = me.data && ["admin", "owner"].includes(me.data.role);
  const del = useMutation({
    mutationFn: (id: string) => api(`documents/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });

  return (
    <>
      <PageHeader title="Document library" subtitle="Everything your organization has uploaded"
        actions={<Link href="/documents/upload"><Button>Upload</Button></Link>} />
      <div className="mb-4 max-w-sm"><Input placeholder="Search titles…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search documents" /></div>
      {del.error && <div className="mb-3"><ErrorBox error={del.error} /></div>}
      {docs.isLoading ? <Spinner /> : docs.error ? <ErrorBox error={docs.error} onRetry={() => docs.refetch()} /> : !docs.data?.items.length ? (
        <Empty title={q ? "No matching documents" : "No documents yet"}>Upload PDFs, scans, images, Word, Excel or PowerPoint files.</Empty>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr><th className="px-4 py-2">Title</th><th className="px-4 py-2">Status</th><th className="px-4 py-2">Pages</th><th className="px-4 py-2">Size</th><th className="px-4 py-2">Version</th><th className="px-4 py-2">Added</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {docs.data.items.map((d) => {
                const v = d.current_version;
                return (
                  <tr key={d.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2">
                      <Link href={`/documents/${d.id}`} className="font-medium text-slate-900 hover:text-brand-700">{d.title}</Link>
                      <div className="mt-0.5 flex gap-1">{d.sensitivity === "confidential" && <Badge tone="warn">Confidential</Badge>}{d.tags.map((t) => <Badge key={t}>{t}</Badge>)}</div>
                    </td>
                    <td className="px-4 py-2">{v ? <DocStatus status={v.status} /> : "—"}{v?.error?.message && <p className="mt-1 max-w-xs text-xs text-bad">{v.error.message}</p>}</td>
                    <td className="px-4 py-2">{v?.page_count ?? "—"}</td>
                    <td className="px-4 py-2">{v ? formatBytes(v.size_bytes) : "—"}</td>
                    <td className="px-4 py-2">v{v?.version_no ?? "—"}</td>
                    <td className="px-4 py-2 text-slate-500">{new Date(d.created_at).toLocaleDateString()}</td>
                    <td className="px-4 py-2 text-right">
                      {canDelete && <Button variant="ghost" onClick={() => confirm(`Delete “${d.title}”? This cannot be undone.`) && del.mutate(d.id)}>Delete</Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
