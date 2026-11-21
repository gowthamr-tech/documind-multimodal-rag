import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { REFRESH, backendAuth, clearSession } from "@/lib/server/session";

export async function POST() {
  const refresh = (await cookies()).get(REFRESH)?.value;
  if (refresh) await backendAuth("logout", { refresh_token: refresh }).catch(() => undefined);
  await clearSession();
  return NextResponse.json({ ok: true });
}
