"use client";
import { useCallback, useRef, useState } from "react";
import { postSse } from "@/lib/sse";
import type { Answer, PendingApproval } from "@/lib/types";

export interface Step { node: string; ms?: number; [k: string]: unknown }
export interface Turn { question: string; steps: Step[]; partial: string; answer?: Answer; pending?: PendingApproval; error?: string }

/** Streams one question through /query (or /query/compare) and accumulates a conversation. */
export function useAsk(endpoint: "query" | "query/compare") {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const conversation = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const patch = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

  const ask = useCallback(async (question: string, documentIds: string[]) => {
    setBusy(true);
    setTurns((ts) => [...ts, { question, steps: [], partial: "" }]);
    abort.current = new AbortController();
    try {
      await postSse(endpoint, { question, document_ids: documentIds, conversation_id: conversation.current, stream: true }, (e) => {
        const data = JSON.parse(e.data);
        if (e.event === "run") conversation.current = data.conversation_id;
        else if (e.event === "agent_step") patch((t) => ({ ...t, steps: [...t.steps, data] }));
        else if (e.event === "answer_delta") patch((t) => ({ ...t, partial: t.partial + data.text }));
        else if (e.event === "final") patch((t) => ({ ...t, answer: data as Answer }));
        else if (e.event === "approval_required") patch((t) => ({ ...t, pending: data as PendingApproval }));
        else if (e.event === "error") patch((t) => ({ ...t, error: data.detail ?? data.title }));
      }, abort.current.signal);
    } catch (err) {
      patch((t) => ({ ...t, error: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  }, [endpoint]);

  const reset = () => { conversation.current = null; setTurns([]); };
  const stop = () => abort.current?.abort();
  return { turns, busy, ask, reset, stop };
}
