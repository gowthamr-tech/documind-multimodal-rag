"use client";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";
import { DocStatus } from "@/components/StatusBadge";
import { Badge, Button, Card, PageHeader } from "@/components/ui";
import { toError } from "@/lib/api";
import { formatBytes } from "@/lib/table";
import type { StatusOut, VersionStatus } from "@/lib/types";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.docx,.xlsx,.pptx";
const MAX = 50 * 1024 * 1024;

interface Item { file: File; state: "pending" | "uploading" | "done" | "error"; message?: string; documentId?: string; status?: VersionStatus; dedup?: boolean; stage?: string }

export default function UploadPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const update = (i: number, patch: Partial<Item>) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  function add(files: FileList | null) {
    if (!files) return;
    setItems((xs) => [...xs, ...Array.from(files).map((file) => ({ file, state: "pending" as const }))]);
  }

  async function poll(i: number, id: string) {
    for (let n = 0; n < 240; n++) {
      const r = await fetch(`/api/proxy/documents/${id}/status`);
      if (r.ok) {
        const s = (await r.json()) as StatusOut;
        const running = s.stages.find((st) => st.status === "running");
        update(i, { status: s.status, stage: running?.stage, message: s.error?.message });
        if (s.status === "ready" || s.status === "failed") break;
      }
      await new Promise((res) => setTimeout(res, 2000));
    }
    qc.invalidateQueries({ queryKey: ["documents"] });
  }

  async function uploadAll() {
    await Promise.all(items.map(async (it, i) => {
      if (it.state !== "pending") return;
      if (it.file.size > MAX) return update(i, { state: "error", message: "File exceeds the 50 MB limit." });
      update(i, { state: "uploading" });
      const form = new FormData();
      form.append("file", it.file);
      const res = await fetch("/api/proxy/documents/upload", { method: "POST", body: form });
      if (!res.ok) {
        const e = await toError(res);
        return update(i, { state: "error", message: e.message });
      }
      const body = await res.json();
      update(i, { state: "done", documentId: body.document_id, status: body.status, dedup: body.deduplicated });
      if (!body.deduplicated) void poll(i, body.document_id);
    }));
  }

  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); };

  return (
    <>
      <PageHeader title="Upload documents" subtitle="PDF, scanned PDF, PNG/JPEG, DOCX, XLSX, PPTX — up to 50 MB each" />
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
        className={`grid place-items-center rounded-lg border-2 border-dashed p-10 text-center ${drag ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-white"}`}>
        <p className="text-sm text-slate-600">Drag files here, or</p>
        <Button variant="secondary" className="mt-2" onClick={() => input.current?.click()}>Choose files</Button>
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => add(e.target.files)} aria-label="Choose files" />
      </div>
      {items.length > 0 && (
        <Card className="mt-4" title={`${items.length} file(s)`} actions={<Button onClick={uploadAll} disabled={!items.some((i) => i.state === "pending")}>Upload</Button>}>
          <ul className="divide-y divide-slate-100">
            {items.map((it, i) => (
              <li key={i} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
                <span className="min-w-0 truncate">{it.file.name} <span className="text-slate-400">· {formatBytes(it.file.size)}</span></span>
                <span className="flex items-center gap-2">
                  {it.state === "uploading" && <Badge tone="info">Uploading…</Badge>}
                  {it.state === "error" && <Badge tone="bad">Rejected</Badge>}
                  {it.dedup && <Badge tone="info" title="Identical content already exists">Already uploaded</Badge>}
                  {it.status && <DocStatus status={it.status} />}
                  {it.stage && it.status === "processing" && <span className="text-xs text-slate-500">{it.stage}</span>}
                  {it.documentId && <Link className="text-brand-700 hover:underline" href={`/documents/${it.documentId}`}>Open</Link>}
                </span>
                {it.message && <p className="w-full text-xs text-bad">{it.message}</p>}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
