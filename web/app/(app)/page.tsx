"use client";

import { Moon, Plane, Home as HomeIcon, Wifi, WifiOff } from "lucide-react";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { RoomSection } from "@/components/RoomSection";
import { applyScene, useConfig, useConnected, useHomeState, useNodes } from "@/lib/home";
import { round, timeAgo } from "@/lib/format";

const SCENE_ICON = { home: HomeIcon, away: Plane, sleep: Moon } as const;

export default function HomePage() {
  const { data: config, loading } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const connected = useConnected();
  const [scene, setScene] = useState<string | null>(null);

  const rooms = useMemo(
    () => Object.entries(config?.rooms ?? {}).sort(([, a], [, b]) => a.order - b.order),
    [config],
  );
  const offlineNodes = Object.entries(nodes ?? {}).filter(([, n]) => !n.online).map(([id]) => id);
  const devicesOn = Object.values(state?.devices ?? {}).filter((d) => d.v === 1).length;

  const statusLine = !connected
    ? "Reconnecting…"
    : offlineNodes.length
      ? `${offlineNodes.length} node offline · ${offlineNodes.join(", ")}`
      : `${devicesOn} on · ${round(state?.power_w, 1)} W now · updated ${timeAgo(state?.updated_at)}`;

  async function runScene(id: string) {
    const s = config?.scenes?.[id];
    if (!s) return;
    setScene(id);
    await applyScene(id, s.set);
    setTimeout(() => setScene(null), 1500);
  }

  if (loading) return <p className="text-muted">Loading home…</p>;
  if (!config)
    return (
      <div className="card p-6">
        <p className="font-semibold">No configuration yet.</p>
        <p className="mt-1 text-sm text-muted">Start the simulator: <code>python pi/sim_house.py</code></p>
      </div>
    );

  return (
    <>
      <PageHeader
        title="My Home"
        sub={<span style={{ color: offlineNodes.length || !connected ? "var(--color-warn)" : undefined }}>{statusLine}</span>}
        right={connected ? <Wifi size={18} className="text-good" /> : <WifiOff size={18} className="text-warn" />}
      />

      {config.scenes && (
        <div className="mt-6 flex gap-2 overflow-x-auto pb-1">
          {Object.entries(config.scenes).map(([id, s]) => {
            const Icon = SCENE_ICON[id as keyof typeof SCENE_ICON] ?? HomeIcon;
            return (
              <button key={id} className="chip" data-active={scene === id} onClick={() => runScene(id)}>
                <Icon size={15} /> {s.name}
              </button>
            );
          })}
        </div>
      )}

      {rooms.map(([id, room]) => (
        <RoomSection
          key={id}
          id={id}
          room={room}
          state={state?.rooms?.[id]}
          devices={state?.devices}
          offline={nodes?.[room.node]?.online === false}
        />
      ))}
    </>
  );
}
