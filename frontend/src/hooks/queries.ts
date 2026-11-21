"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { DocumentDetail, DocumentSummary, Page } from "@/lib/types";

export interface Me { user_id: string; email: string; org_id: string; org_name: string; role: string }

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<Me>("auth/me"), staleTime: 60_000 });

export const useDocuments = (params: Record<string, string> = {}) =>
  useQuery({
    queryKey: ["documents", params],
    queryFn: () => api<Page<DocumentSummary>>(`documents?${new URLSearchParams({ limit: "100", ...params })}`),
    // keep polling while anything is still processing
    refetchInterval: (q) =>
      q.state.data?.items.some((d) => d.current_version && ["queued", "processing", "uploaded"].includes(d.current_version.status)) ? 2500 : false,
  });

export const useDocument = (id: string) =>
  useQuery({ queryKey: ["document", id], queryFn: () => api<DocumentDetail>(`documents/${id}`) });
