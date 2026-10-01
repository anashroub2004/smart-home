"use client";

import { RequireAuth } from "@/lib/auth";
import { TabBar } from "@/components/TabBar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <TabBar />
      <main className="px-4 pb-28 pt-4 md:pb-12 md:pl-66 md:pr-10 md:pt-8">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </RequireAuth>
  );
}
