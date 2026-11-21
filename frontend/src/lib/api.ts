// Browser-side API client. All calls go through the Next.js BFF (/api/proxy/*), which holds the
// session in httpOnly cookies — this code never sees a token.
export class ApiError extends Error {
  constructor(public status: number, public title: string, public detail?: string, public requestId?: string) {
    super(detail ?? title);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/proxy/${path.replace(/^\//, "")}`, { ...init, credentials: "same-origin" });
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function toError(res: Response): Promise<ApiError> {
  try {
    const p = await res.json();
    return new ApiError(res.status, p.title ?? res.statusText, p.detail ?? undefined, p.request_id);
  } catch {
    return new ApiError(res.status, res.statusText);
  }
}

export const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
});

export const fileUrl = (path: string) => `/api/proxy/${path.replace(/^\//, "")}`;
