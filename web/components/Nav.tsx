"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

const TABS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/reports", label: "Rapports" },
  { href: "/messages", label: "Journal" },
  { href: "/sites", label: "Sites" },
  { href: "/predictions", label: "Aide à la décision" },
  { href: "/forecast", label: "Prévisions" },
];

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  async function handleLogout() {
    await supabase.auth.signOut();
    router.replace("/login");
  }

  const activeLabel =
    TABS.find((t) => t.href === pathname)?.label || "OpsLens";

  return (
    <header className="sticky top-0 z-20 border-b border-zinc-200 bg-white/90 backdrop-blur dark:border-zinc-800 dark:bg-black/90">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
        {/* Brand */}
        <Link
          href="/dashboard"
          className="text-lg font-semibold text-zinc-900 dark:text-zinc-50"
        >
          OpsLens
        </Link>

        {/* Onglets desktop (md+) */}
        <nav className="hidden flex-1 justify-center gap-1 md:flex">
          {TABS.map((tab) => {
            const active = pathname === tab.href;
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  active
                    ? "bg-zinc-900 text-white dark:bg-zinc-50 dark:text-zinc-900"
                    : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>

        {/* Bouton déconnexion desktop */}
        <button
          onClick={handleLogout}
          className="hidden rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 md:inline-flex dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
        >
          Déconnexion
        </button>

        {/* Hamburger mobile */}
        <div className="flex items-center gap-2 md:hidden">
          <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
            {activeLabel}
          </span>
          <button
            onClick={() => setOpen((v) => !v)}
            aria-label="Ouvrir le menu"
            className="rounded-md border border-zinc-300 p-2 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
          >
            {open ? (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            ) : (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="3" y1="6" x2="21" y2="6" />
                <line x1="3" y1="12" x2="21" y2="12" />
                <line x1="3" y1="18" x2="21" y2="18" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Drawer mobile (drop-down) */}
      {open && (
        <div className="border-t border-zinc-200 bg-white md:hidden dark:border-zinc-800 dark:bg-black">
          <nav className="flex flex-col">
            {TABS.map((tab) => {
              const active = pathname === tab.href;
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  onClick={() => setOpen(false)}
                  className={`border-b border-zinc-100 px-4 py-3 text-sm font-medium dark:border-zinc-900 ${
                    active
                      ? "bg-zinc-900 text-white dark:bg-zinc-50 dark:text-zinc-900"
                      : "text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-900"
                  }`}
                >
                  {tab.label}
                </Link>
              );
            })}
            <button
              onClick={() => {
                setOpen(false);
                handleLogout();
              }}
              className="px-4 py-3 text-left text-sm font-medium text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-900"
            >
              Déconnexion
            </button>
          </nav>
        </div>
      )}
    </header>
  );
}
