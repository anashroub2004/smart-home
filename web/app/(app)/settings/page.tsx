"use client";

import { PageHeader } from "@/components/PageHeader";
import { useAuth } from "@/lib/auth";
import { USE_EMULATOR } from "@/lib/firebase";
import { useConfig, useNodes } from "@/lib/home";
import { timeAgo } from "@/lib/format";

export default function SettingsPage() {
  const { user, signOut } = useAuth();
  const { data: config } = useConfig();
  const { data: nodes } = useNodes();

  return (
    <>
      <PageHeader title="Settings" sub={user?.email ?? undefined} />

      <h2 className="mb-2 mt-6 text-lg font-bold">Hub & nodes</h2>
      <ul className="card divide-y divide-border px-4">
        {Object.entries(config?.nodes ?? {}).map(([id, n]) => {
          const s = nodes?.[id];
          const synced = s?.config_version === config?.version;
          return (
            <li key={id} className="flex items-center gap-3 py-3 text-sm">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: s?.online ? "var(--color-good)" : "var(--color-bad)" }} />
              <span className="flex-1">
                {n.name} <span className="text-muted">· {id}</span>
              </span>
              <span className="text-right text-xs text-muted">
                {s?.online ? "Online" : `Offline · last seen ${timeAgo(s?.last_seen)}`}
                <br />
                config v{s?.config_version ?? "?"} {synced ? "✓" : "· syncing"}
                {s?.rssi !== undefined ? ` · ${s.rssi} dBm` : ""}
              </span>
            </li>
          );
        })}
      </ul>

      <h2 className="mb-2 mt-8 text-lg font-bold">System</h2>
      <div className="card space-y-2 p-4 text-sm">
        <p className="flex justify-between"><span className="text-muted">Config version</span><span>v{config?.version ?? "—"}</span></p>
        <p className="flex justify-between"><span className="text-muted">Data source</span><span>{USE_EMULATOR ? "Local emulator" : "Firebase"}</span></p>
        <p className="flex justify-between"><span className="text-muted">Rooms</span><span>{Object.keys(config?.rooms ?? {}).length}</span></p>
      </div>

      <button onClick={signOut} className="mt-8 w-full rounded-2xl border border-border py-3 text-bad">
        Sign out
      </button>
    </>
  );
}
