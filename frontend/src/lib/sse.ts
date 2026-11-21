// Minimal, spec-conformant Server-Sent Events parser for fetch() streams (EventSource can't POST).
export interface SseEvent { event: string; data: string }

export function createSseParser(onEvent: (e: SseEvent) => void) {
  let buf = "";
  return (chunk: string) => {
    buf += chunk.replace(/\r\n?/g, "\n");
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;
        const [field, ...rest] = line.split(":");
        const value = rest.join(":").replace(/^ /, "");
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
      }
      if (data.length) onEvent({ event, data: data.join("\n") });
    }
  };
}

export async function postSse(path: string, body: unknown, onEvent: (e: SseEvent) => void, signal?: AbortSignal) {
  const res = await fetch(`/api/proxy/${path}`, {
    method: "POST", headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body), signal,
  });
  if (!res.ok || !res.body) {
    const { toError } = await import("./api");
    throw await toError(res);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const feed = createSseParser(onEvent);
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    feed(value);
  }
}
