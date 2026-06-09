"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) {
        router.replace("/dashboard");
      } else {
        router.replace("/login");
      }
    });
  }, [router]);

  return (
    <div className="flex h-screen items-center justify-center bg-zinc-50 dark:bg-black">
      <p className="text-zinc-500 dark:text-zinc-400">Chargement…</p>
    </div>
  );
}
