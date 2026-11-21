import { NextResponse } from "next/server";
import { backendAuth, setSession, type TokenPair } from "@/lib/server/session";

export async function POST(req: Request) {
  const res = await backendAuth("login", await req.json());
  const body = await res.json();
  if (!res.ok) return NextResponse.json(body, { status: res.status });
  await setSession(body as TokenPair);
  return NextResponse.json({ ok: true });
}
