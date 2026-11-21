"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { cx, Spinner } from "@/components/ui";
import { useMe } from "@/hooks/queries";
import { ApiError } from "@/lib/api";

const NAV = [
  ["/", "Dashboard"], ["/documents", "Documents"], ["/documents/upload", "Upload"], ["/chat", "AI Chat"],
  ["/compare", "Compare"], ["/tables", "Extracted tables"], ["/charts", "Chart analysis"],
  ["/processing", "Processing history"], ["/evaluations", "Evaluation"],
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const me = useMe();

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [me.error, path, router]);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (me.isLoading) return <div className="grid h-screen place-items-center"><Spinner /></div>;

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="px-4 py-4 text-base font-semibold text-slate-900">DocuMind <span className="text-brand-600">AI</span></div>
        <nav className="flex-1 space-y-0.5 px-2" aria-label="Main">
          {NAV.map(([href, label]) => {
            const active = href === "/" ? path === "/" : path === href || (path.startsWith(href) && !(href === "/documents" && path.startsWith("/documents/upload")));
            return (
              <Link key={href} href={href} aria-current={active ? "page" : undefined}
                className={cx("block rounded-md px-3 py-2 text-sm", active ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-50")}>
                {label}
              </Link>
            );
          })}
        </nav>
        {me.data && (
          <div className="border-t border-slate-100 p-3 text-xs text-slate-500">
            <p className="truncate font-medium text-slate-700">{me.data.email}</p>
            <p className="truncate">{me.data.org_name} · {me.data.role}</p>
            <button onClick={logout} className="mt-2 text-brand-700 hover:underline">Sign out</button>
          </div>
        )}
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8">{children}</main>
    </div>
  );
}
