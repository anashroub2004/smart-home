"use client";

// Add a room (rare). A room belongs to one ESP32 node and lists which sensors it has.

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ScreenHeader } from "@/components/ScreenHeader";
import { useToast } from "@/components/Toast";
import { addRoom, useConfig } from "@/lib/home";
import { slugId } from "@/lib/view";

const SENSORS = [
  { id: "climate", label: "Temperature & humidity", sub: "SHT31", keys: ["temp", "hum"], hw: "SHT31" },
  { id: "lux", label: "Light level", sub: "BH1750", keys: ["lux"], hw: "BH1750" },
  { id: "pir", label: "Motion", sub: "PIR", keys: ["occ"], hw: "PIR" },
  { id: "mmwave", label: "Presence (even when still)", sub: "C1001 mmWave", keys: ["occ"], hw: "C1001 mmWave" },
];

export default function AddRoomPage() {
  const { data: config } = useConfig();
  const router = useRouter();
  const toast = useToast();
  const [name, setName] = useState("");
  const [node, setNode] = useState<string>("");
  const [picked, setPicked] = useState<string[]>(["pir"]);
  const [saving, setSaving] = useState(false);

  if (!config) return <div className="skeleton" />;
  const nodeIds = Object.keys(config.nodes);
  const chosenNode = node || nodeIds[0];

  async function save() {
    if (!config || !name.trim()) return;
    setSaving(true);
    const id = slugId(name, (x) => x in config.rooms);
    const sel = SENSORS.filter((s) => picked.includes(s.id));
    const order = Math.max(0, ...Object.values(config.rooms).map((r) => r.order)) + 1;
    try {
      await addRoom(config, id, {
        name: name.trim(),
        node: chosenNode,
        order,
        sensors: [...new Set(sel.flatMap((s) => s.keys))],
        hardware: sel.map((s) => s.hw),
        devices: {},
      });
      router.push(`/room?id=${id}`);
    } catch {
      setSaving(false);
      toast("Could not save. Check your connection.");
    }
  }

  return (
    <>
      <ScreenHeader title="Add room" back="/settings" />
      <section className="card" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <label className="field">
          1 · Name
          <input type="text" value={name} placeholder="Balcony" onChange={(e) => setName(e.target.value)} aria-label="Room name" />
        </label>
        <div>
          <div className="h-label">2 · Which node is in this room?</div>
          <div className="scenes" role="group" aria-label="Node" style={{ flexWrap: "wrap" }}>
            {nodeIds.map((n) => (
              <button key={n} type="button" className={chosenNode === n ? "scene on" : "scene"} onClick={() => setNode(n)}>{config.nodes[n].name}</button>
            ))}
          </div>
        </div>
        <div>
          <div className="h-label">3 · Sensors in the room</div>
          {SENSORS.map((s) => {
            const on = picked.includes(s.id);
            return (
              <div key={s.id} className="sw-row">
                <div className="txt">{s.label}<small>{s.sub}</small></div>
                <button type="button" className={on ? "sw on" : "sw"} aria-pressed={on} aria-label={s.label}
                  onClick={() => setPicked(on ? picked.filter((x) => x !== s.id) : [...picked, s.id])}>
                  <span className="k" />
                </button>
              </div>
            );
          })}
        </div>
        <button type="button" className="btn btn-light btn-block" disabled={saving || !name.trim()} onClick={save}>
          {saving && <span className="spin" />}
          {saving ? "Saving…" : "Add room"}
        </button>
      </section>
    </>
  );
}
