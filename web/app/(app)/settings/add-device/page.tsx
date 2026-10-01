"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ICheck } from "@/components/Icons";
import { ScreenHeader } from "@/components/ScreenHeader";
import { useToast } from "@/components/Toast";
import type { DeviceType } from "@/lib/contract";
import { addDevice, removeDevice, useConfig, useNodes } from "@/lib/home";
import { sortedRooms } from "@/lib/view";

const TYPES: { id: Exclude<DeviceType, "lock">; label: string; watts: number }[] = [
  { id: "fan", label: "Fan", watts: 2.4 },
  { id: "light", label: "Light", watts: 1.2 },
  { id: "washer", label: "Washer", watts: 3.6 },
];
const PINS = [4, 5, 13, 14, 18, 19, 23, 25, 26, 27];
const INAS = ["none", "0x40", "0x41", "0x42"];
const CONFIRM_MS = 10_000;

type Status = "form" | "saving" | "done";

export default function AddDevicePage() {
  const toast = useToast();
  const { data: config } = useConfig();
  const { data: nodes } = useNodes();
  const rooms = sortedRooms(config);
  const latestConfig = useRef(config);
  latestConfig.current = config;

  const [type, setType] = useState<(typeof TYPES)[number]["id"]>("light");
  const [roomId, setRoomId] = useState("kitchen");
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [pin, setPin] = useState<number | null>(null);
  const [ina, setIna] = useState("none");
  const [status, setStatus] = useState<Status>("form");
  const waiting = useRef<{ version: number; node: string; id: string; timer: ReturnType<typeof setTimeout> } | null>(null);

  const room = config?.rooms?.[roomId] ?? (rooms[0] ? config?.rooms?.[rooms[0][0]] : undefined);
  const node = room?.node ?? "";

  // pins + INA226 addresses already taken on this node (reserved by sensors, or used by another device)
  const { usedPins, usedInas } = useMemo(() => {
    const pins = new Set<number>(config?.nodes?.[node]?.reserved_pins ?? []);
    const inas = new Set<string>();
    for (const r of Object.values(config?.rooms ?? {})) {
      if (r.node !== node) continue;
      for (const d of Object.values(r.devices ?? {})) {
        pins.add(d.pin);
        if (d.button !== undefined) pins.add(d.button);
        if (d.ina) inas.add(d.ina);
      }
    }
    return { usedPins: pins, usedInas: inas };
  }, [config, node]);

  // sensible defaults whenever the room or type changes
  useEffect(() => {
    if (!room) return;
    if (!nameEdited) setName(`${room.name} ${TYPES.find((t) => t.id === type)!.label.toLowerCase()}`);
    if (pin === null || usedPins.has(pin)) setPin(PINS.find((p) => !usedPins.has(p)) ?? null);
    if (ina !== "none" && usedInas.has(ina)) setIna("none");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, type, room?.name, usedPins, usedInas]);

  // wait for the node to confirm the new config version
  useEffect(() => {
    const w = waiting.current;
    if (!w) return;
    if ((nodes?.[w.node]?.config_version ?? 0) >= w.version) {
      clearTimeout(w.timer);
      waiting.current = null;
      setStatus("done");
    }
  }, [nodes]);
  useEffect(() => () => waiting.current?.timer && clearTimeout(waiting.current.timer), []);

  if (!config || !room) return <div className="skeleton" />;

  async function save() {
    if (!config || !room || pin === null || !name.trim()) return;
    setStatus("saving");
    const base = `${roomId}_${type}`;
    let id = base;
    for (let n = 2; Object.values(config.rooms).some((r) => r.devices?.[id]); n++) id = `${base}_${n}`;
    const t = TYPES.find((x) => x.id === type)!;
    try {
      const version = await addDevice(config, roomId, id, {
        type, name: name.trim(), pin, watts: t.watts, added_at: Date.now(), ...(ina !== "none" ? { ina } : {}),
      });
      const timer = setTimeout(async () => {
        waiting.current = null;
        setStatus("form");
        toast(`The ${node} node didn't confirm. The device was not added.`);
        const latest = latestConfig.current;
        if (latest?.rooms?.[roomId]?.devices?.[id]) await removeDevice(latest, roomId, id);
      }, CONFIRM_MS);
      waiting.current = { version, node, id, timer };
    } catch {
      setStatus("form");
      toast("Could not save. Check your connection.");
    }
  }

  function another() {
    setStatus("form");
    setNameEdited(false);
    setPin(null);
    setIna("none");
  }

  const typeLabel = TYPES.find((t) => t.id === type)!.label.toLowerCase();
  const hint = `connect the ${type === "light" ? "relay" : "MOSFET module"} signal to GPIO ${pin ?? "?"} on the ${node} node, share GND with the ESP32, and power the ${typeLabel} from its own supply.`;

  return (
    <>
      <ScreenHeader title="Add device" back="/settings" />

      {status === "done" ? (
        <section className="card" style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 10, padding: "28px 18px" }}>
          <div style={{ width: 64, height: 64, borderRadius: "50%", background: "#13261B", color: "#34C759", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <ICheck size={30} sw={2.2} />
          </div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{name} is connected</div>
          <div className="muted" style={{ fontSize: 14 }}>
            The {node} node confirmed GPIO {pin}. The AI will start predicting it after about 3 weeks of use.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap", justifyContent: "center" }}>
            <Link href={`/room?id=${roomId}`} className="btn btn-light">Open {room.name}</Link>
            <button type="button" className="btn" onClick={another}>Add another</button>
          </div>
        </section>
      ) : (
        <section className="card" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div>
            <div className="h-label">1 · What is it?</div>
            <div className="seg" role="group" aria-label="Device type">
              {TYPES.map((t) => (
                <button key={t.id} type="button" className={type === t.id ? "on" : ""} onClick={() => setType(t.id)}>{t.label}</button>
              ))}
            </div>
          </div>

          <label className="field">
            2 · Name
            <input type="text" value={name} aria-label="Device name" onChange={(e) => { setName(e.target.value); setNameEdited(true); }} />
          </label>

          <div>
            <div className="h-label">3 · Which room?</div>
            <div className="scenes" role="group" aria-label="Room" style={{ flexWrap: "wrap" }}>
              {rooms.map(([id, r]) => (
                <button key={id} type="button" className={roomId === id ? "scene on" : "scene"} aria-pressed={roomId === id} onClick={() => setRoomId(id)}>{r.name}</button>
              ))}
            </div>
            <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>
              Wired to the <b style={{ color: "#F2F3F5" }}>{node}</b> node
            </div>
          </div>

          <div>
            <div className="h-label">4 · Which pin did you wire it to?</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {PINS.map((p) => {
                const used = usedPins.has(p);
                return (
                  <button key={p} type="button" className={pin === p && !used ? "scene on" : "scene"} aria-pressed={pin === p}
                    disabled={used} onClick={() => setPin(p)} aria-label={`GPIO ${p}${used ? ", in use" : ""}`}
                    style={{ minWidth: 64, justifyContent: "center", opacity: used ? 0.35 : 1, cursor: used ? "not-allowed" : "pointer" }}>
                    GPIO {p}
                  </button>
                );
              })}
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>Greyed-out pins are already used on this node.</div>
          </div>

          <div>
            <div className="h-label">5 · Power sensor (optional)</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {INAS.map((a) => {
                const used = usedInas.has(a);
                return (
                  <button key={a} type="button" className={ina === a && !used ? "scene on" : "scene"} aria-pressed={ina === a}
                    disabled={used} onClick={() => setIna(a)} style={{ opacity: used ? 0.35 : 1, cursor: used ? "not-allowed" : "pointer" }}>
                    {a === "none" ? "None" : `INA226 ${a}`}
                  </button>
                );
              })}
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>An INA226 on the same I2C bus. Without one, usage is estimated.</div>
          </div>

          <div style={{ borderRadius: 14, background: "#1C1F24", padding: "12px 14px", fontSize: 13.5, color: "#C5CAD1" }}>
            <b style={{ color: "#F2F3F5" }}>Before saving:</b> {hint}
          </div>

          <button type="button" className="btn btn-light btn-block" disabled={status === "saving" || pin === null || !name.trim()} onClick={save}>
            {status === "saving" && <span className="spin" />}
            {status === "saving" ? `Sending to the ${node} node…` : "Save and connect"}
          </button>
        </section>
      )}
    </>
  );
}
