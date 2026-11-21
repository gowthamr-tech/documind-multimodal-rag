"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { DocSelect } from "@/features/DocSelect";
import { TableView } from "@/features/TableView";
import { api } from "@/lib/api";
import type { TableInfo } from "@/lib/types";

export default function TablesPage() {
  const [doc, setDoc] = useState("");
  const tables = useQuery({ queryKey: ["tables", doc], queryFn: () => api<TableInfo[]>(`documents/${doc}/tables`), enabled: !!doc });
  return (
    <>
      <PageHeader title="Extracted tables" subtitle="Structure, merged headers, units and footnotes as recovered from the document" />
      <DocSelect value={doc} onChange={setDoc} />
      <div className="mt-4 space-y-6">
        {!doc ? <Empty title="Choose a document to see its tables" /> : tables.isLoading ? <Spinner /> : tables.error ? <ErrorBox error={tables.error} />
          : !tables.data?.length ? <Empty title="No tables were found in this document" />
          : tables.data.map((t) => (
            <div key={t.id} className="rounded-lg border border-slate-200 bg-white p-4">
              <TableView t={t} />
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer text-slate-600">Cell semantics ({t.cells.filter((c) => c.numeric_value != null).length} numeric cells)</summary>
                <ul className="mt-1 max-h-48 space-y-0.5 overflow-y-auto font-mono">
                  {t.cells.filter((c) => c.numeric_value != null).map((c, i) => (
                    <li key={i}>{[...c.row_path, ...c.header_path].join(" › ")} = {c.numeric_value} {c.unit ?? ""}{c.scale ? ` ×${c.scale.toLocaleString()}` : ""}</li>
                  ))}
                </ul>
              </details>
            </div>
          ))}
      </div>
    </>
  );
}
