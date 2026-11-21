"use client";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { DocStatus } from "@/components/StatusBadge";
import { Badge, Button, cx, Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { FigureCard } from "@/features/FigureCard";
import { TableView } from "@/features/TableView";
import { useDocument } from "@/hooks/queries";
import { api, fileUrl } from "@/lib/api";
import { bboxStyle, parseHighlight } from "@/lib/answer";
import type { FigureInfo, PageInfo, StatusOut, TableInfo } from "@/lib/types";

interface ElementInfo { id: string; type: string; page_no: number | null; bbox: number[] | null; text: string; section_path: string[] }
type Tab = "page" | "tables" | "charts" | "processing";

function Viewer() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const page = Math.max(1, Number(sp.get("page") ?? 1));
  const hl = parseHighlight(sp.get("hl"));
  const [tab, setTab] = useState<Tab>("page");
  useEffect(() => { if (sp.get("page")) setTab("page"); }, [sp]);

  const doc = useDocument(id);
  const pages = useQuery({ queryKey: ["pages", id], queryFn: () => api<PageInfo[]>(`documents/${id}/pages`) });
  const elements = useQuery({ queryKey: ["elements", id, page], queryFn: () => api<ElementInfo[]>(`documents/${id}/elements?page=${page}`) });
  const tables = useQuery({ queryKey: ["tables", id], queryFn: () => api<TableInfo[]>(`documents/${id}/tables`), enabled: tab === "tables" });
  const figures = useQuery({ queryKey: ["figures", id], queryFn: () => api<FigureInfo[]>(`documents/${id}/charts`), enabled: tab === "charts" });
  const status = useQuery({ queryKey: ["status", id], queryFn: () => api<StatusOut>(`documents/${id}/status`), enabled: tab === "processing" });

  const total = pages.data?.length ?? 0;
  const current = pages.data?.find((p) => p.page_no === page);
  const go = (p: number) => router.replace(`/documents/${id}?page=${p}`);

  if (doc.isLoading) return <Spinner />;
  if (doc.error) return <ErrorBox error={doc.error} />;
  const d = doc.data!;
  const v = d.current_version;

  return (
    <>
      <PageHeader title={d.title}
        subtitle={v ? `${v.original_filename} · v${v.version_no} · ${v.page_count ?? "?"} page(s)` : undefined}
        actions={<>
          {v && <DocStatus status={v.status} />}
          <a href={fileUrl(`documents/${id}/file`)} target="_blank" rel="noreferrer"><Button variant="secondary">Original</Button></a>
          <Link href={`/chat?doc=${id}`}><Button>Ask about this document</Button></Link>
        </>} />
      <div role="tablist" className="mb-4 flex gap-1 border-b border-slate-200">
        {(["page", "tables", "charts", "processing"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={cx("-mb-px border-b-2 px-3 py-2 text-sm capitalize", tab === t ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800")}>
            {t === "page" ? "Pages" : t}
          </button>
        ))}
      </div>

      {tab === "page" && (pages.isLoading ? <Spinner /> : !total ? (
        <Empty title="No page images">{v?.status === "ready" ? "This format has no rendered pages (e.g. DOCX without LibreOffice)." : "Processing…"}</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <Button variant="secondary" disabled={page <= 1} onClick={() => go(page - 1)} aria-label="Previous page">‹</Button>
              <span className="text-sm text-slate-600">Page {page} of {total}</span>
              <Button variant="secondary" disabled={page >= total} onClick={() => go(page + 1)} aria-label="Next page">›</Button>
              {current?.ocr_applied && <Badge tone="info">OCR</Badge>}
              {hl && <Badge tone="warn">Cited region highlighted</Badge>}
            </div>
            <div className="relative overflow-hidden rounded border border-slate-200 bg-white shadow-sm">
              {current?.has_image ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={fileUrl(`documents/${id}/pages/${page}/image`)} alt={`Page ${page}`} className="block w-full" />
              ) : <div className="p-10 text-center text-sm text-slate-500">No image for this page</div>}
              {hl && <div className="pointer-events-none absolute rounded-sm bg-amber-300/30 ring-2 ring-amber-500" style={bboxStyle(hl)} />}
            </div>
          </div>
          <aside className="max-h-[80vh] space-y-2 overflow-y-auto">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Extracted text on this page</p>
            {elements.data?.map((e) => (
              <div key={e.id} className="rounded border border-slate-200 bg-white p-2 text-xs">
                <span className="mr-1 font-mono text-slate-400">{e.type}</span>{e.text.slice(0, 300) || <em className="text-slate-400">(no text)</em>}
              </div>
            ))}
          </aside>
        </div>
      ))}

      {tab === "tables" && (tables.isLoading ? <Spinner /> : tables.error ? <ErrorBox error={tables.error} /> : !tables.data?.length ? <Empty title="No tables found" /> : (
        <div className="space-y-6">{tables.data.map((t) => <div key={t.id} className="rounded-lg border border-slate-200 bg-white p-4"><TableView t={t} /></div>)}</div>
      ))}

      {tab === "charts" && (figures.isLoading ? <Spinner /> : figures.error ? <ErrorBox error={figures.error} /> : !figures.data?.length ? <Empty title="No figures found" /> : (
        <div className="space-y-4">{figures.data.map((f) => <FigureCard key={f.id} docId={id} f={f} />)}</div>
      ))}

      {tab === "processing" && (status.isLoading ? <Spinner /> : status.data && (
        <ol className="space-y-2">
          {status.data.stages.map((s, i) => (
            <li key={i} className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-200 bg-white px-3 py-2 text-sm">
              <span className="font-medium">{s.stage}</span>
              <span className="flex items-center gap-2 text-xs text-slate-500">
                <Badge tone={s.status === "succeeded" ? "ok" : s.status === "failed" ? "bad" : "neutral"}>{s.status}</Badge>
                {typeof s.metrics.duration_ms === "number" && `${(s.metrics.duration_ms / 1000).toFixed(2)}s`}
                <code className="hidden md:inline">{JSON.stringify(Object.fromEntries(Object.entries(s.metrics).filter(([k]) => k !== "duration_ms")))}</code>
              </span>
              {s.error?.message && <p className="w-full text-xs text-bad">{s.error.message}</p>}
            </li>
          ))}
        </ol>
      ))}
    </>
  );
}

export default function DocumentPage() {
  return <Suspense fallback={<Spinner />}><Viewer /></Suspense>;
}
