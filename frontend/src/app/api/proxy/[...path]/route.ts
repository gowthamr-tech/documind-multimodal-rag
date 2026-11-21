// Authenticated pass-through to the FastAPI backend. Streams bodies both ways (uploads, SSE,
// images) and transparently rotates the access token once on 401.
import { cookies } from "next/headers";
import { ACCESS, API_URL, REFRESH, backendAuth, clearSession, setSession, type TokenPair } from "@/lib/server/session";

export const dynamic = "force-dynamic";

const PASS_REQ = ["content-type", "accept", "x-request-id"];
const PASS_RES = ["content-type", "content-disposition", "cache-control", "x-request-id", "retry-after",
  "ratelimit-limit", "ratelimit-remaining"];

async function forward(req: Request, path: string[], token: string | undefined, body: ArrayBuffer | null) {
  const url = new URL(req.url);
  const target = `${API_URL}/api/v1/${path.map(encodeURIComponent).join("/")}${url.search}`;
  const headers = new Headers();
  for (const h of PASS_REQ) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  if (token) headers.set("authorization", `Bearer ${token}`);
  return fetch(target, { method: req.method, headers, body, cache: "no-store", redirect: "manual" });
}

async function handle(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const jar = await cookies();
  const body = req.method === "GET" || req.method === "HEAD" ? null : await req.arrayBuffer();
  let res = await forward(req, path, jar.get(ACCESS)?.value, body);

  if (res.status === 401) {
    const refresh = jar.get(REFRESH)?.value;
    if (refresh) {
      const r = await backendAuth("refresh", { refresh_token: refresh });
      if (r.ok) {
        const pair = (await r.json()) as TokenPair;
        await setSession(pair);
        res = await forward(req, path, pair.access_token, body);
      } else {
        await clearSession();
      }
    }
  }

  const out = new Headers();
  for (const h of PASS_RES) {
    const v = res.headers.get(h);
    if (v) out.set(h, v);
  }
  if (out.get("content-type")?.startsWith("text/event-stream")) out.set("x-accel-buffering", "no");
  return new Response(res.body, { status: res.status, headers: out });
}

export { handle as GET, handle as POST, handle as DELETE, handle as PUT, handle as PATCH };
