// Server-only helpers for the BFF: tokens live in httpOnly cookies, never in browser JS.
import { cookies } from "next/headers";

export const API_URL = (process.env.DOCUMIND_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const ACCESS = "dm_access";
export const REFRESH = "dm_refresh";
const secure = process.env.NODE_ENV === "production";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export async function setSession(t: TokenPair): Promise<void> {
  const jar = await cookies();
  jar.set(ACCESS, t.access_token, { httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: t.expires_in });
  jar.set(REFRESH, t.refresh_token, {
    httpOnly: true, sameSite: "strict", secure, path: "/api", maxAge: 14 * 24 * 3600,
  });
}

export async function clearSession(): Promise<void> {
  const jar = await cookies();
  jar.delete(ACCESS);
  jar.delete({ name: REFRESH, path: "/api" });
}

export async function backendAuth(path: string, body: unknown): Promise<Response> {
  return fetch(`${API_URL}/api/v1/auth/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
}
