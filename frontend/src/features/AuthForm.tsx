"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button, ErrorBox, Field, Input } from "@/components/ui";
import { toError } from "@/lib/api";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const f = new FormData(e.currentTarget);
    const body = Object.fromEntries(f.entries());
    const res = await fetch(`/api/auth/${mode}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    setBusy(false);
    if (!res.ok) return setError(await toError(res));
    // only same-site relative redirects (no open redirect)
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div>
          <h1 className="text-lg font-semibold">DocuMind <span className="text-brand-600">AI</span></h1>
          <p className="text-sm text-slate-500">{mode === "login" ? "Sign in to your workspace" : "Create a workspace"}</p>
        </div>
        <Field label="Email"><Input name="email" type="email" autoComplete="email" required /></Field>
        <Field label="Password" hint={mode === "register" ? "At least 12 characters" : undefined}>
          <Input name="password" type="password" minLength={mode === "register" ? 12 : 1} autoComplete={mode === "login" ? "current-password" : "new-password"} required />
        </Field>
        {mode === "register" && <Field label="Organization name"><Input name="org_name" minLength={2} required /></Field>}
        {error != null && <ErrorBox error={error} />}
        <Button type="submit" disabled={busy} className="w-full">{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</Button>
        <p className="text-center text-sm text-slate-500">
          {mode === "login" ? <>No account? <Link className="text-brand-700 hover:underline" href="/register">Register</Link></>
            : <>Have an account? <Link className="text-brand-700 hover:underline" href="/login">Sign in</Link></>}
        </p>
      </form>
    </div>
  );
}
