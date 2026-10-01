"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ChevronLeft } from "lucide-react";
import { DeviceTile } from "@/components/DeviceTile";
import { EventRow } from "@/components/EventRow";
import { RoomStats } from "@/components/RoomSection";
import { sendCommand, useConfig, useEvents, useHomeState, useNodes } from "@/lib/home";

// Static export can't pre-render /room/[id] for rooms added later, so the room id is a query param: /room?id=living
export default function RoomPage() {
  return (
    <Suspense fallback={<p className="text-muted">Loading…</p>}>
      <Room />
    </Suspense>
  );
}

function Room() {
  const id = useSearchParams().get("id") ?? "";
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { list: events } = useEvents(200);
  const room = config?.rooms?.[id];

  if (!config) return <p className="text-muted">Loading…</p>;
  if (!room) return <p className="text-muted">Room “{id}” not found.</p>;

  const offline = nodes?.[room.node]?.online === false;
  const devices = Object.entries(room.devices ?? {});
  const roomEvents = events.filter((e) => e.room === id).slice(0, 15);

  return (
    <>
      <Link href="/" className="mb-4 inline-flex items-center gap-1 text-sm text-muted"><ChevronLeft size={16} /> Home</Link>
      <h1 className="text-[28px] font-bold">{room.name}</h1>
      <RoomStats state={state?.rooms?.[id]} />

      {devices.length > 0 && (
        <div className="tiles mt-6">
          {devices.map(([dev, cfg]) => (
            <DeviceTile key={dev} id={dev} cfg={cfg} state={state?.devices?.[dev]} offline={offline} />
          ))}
        </div>
      )}

      {devices
        .filter(([, cfg]) => cfg.type === "fan")
        .map(([dev, cfg]) => (
          <FanSpeed key={dev} id={dev} name={cfg.name} speed={state?.devices?.[dev]?.speed ?? 60} disabled={offline} />
        ))}

      <h2 className="mt-8 mb-2 text-lg font-bold">Recent activity</h2>
      {roomEvents.length ? (
        <ul className="card px-4">{roomEvents.map((e) => <EventRow key={e.id} e={e} />)}</ul>
      ) : (
        <p className="text-sm text-muted">Nothing yet.</p>
      )}
    </>
  );
}

function FanSpeed({ id, name, speed, disabled }: { id: string; name: string; speed: number; disabled: boolean }) {
  const [val, setVal] = useState(speed);
  return (
    <div className="card mt-4 p-4">
      <div className="flex justify-between text-sm">
        <span>{name} speed</span>
        <span className="text-muted">{val}%</span>
      </div>
      <input
        type="range" min={20} max={100} step={10} value={val} disabled={disabled}
        onChange={(e) => setVal(Number(e.target.value))}
        onPointerUp={() => sendCommand(id, 1, val)}
        onKeyUp={() => sendCommand(id, 1, val)}
        className="mt-3 w-full accent-[var(--color-fan)]"
      />
    </div>
  );
}
