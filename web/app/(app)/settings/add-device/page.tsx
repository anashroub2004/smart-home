"use client";

// Add ANY device: pick a template (or Custom), then adjust what it can do, who may change it,
// which automation applies and how it is wired. Saved to /config; the node must confirm the new version.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DeviceIcon } from "@/components/DeviceIcon";
import { ICheck } from "@/components/Icons";
import { ScreenHeader } from "@/components/ScreenHeader";
import { useToast } from "@/components/Toast";
import type { DeviceConfig, DeviceControl, DeviceHw, DeviceRules } from "@/lib/contract";
import { addDevice, removeDevice, useConfig, useNodes } from "@/lib/home";
import { TEMPLATES, templateById } from "@/lib/templates";
import { slugId, sortedRooms } from "@/lib/view";

const PINS = [4, 5, 13, 14, 18, 19, 23, 25, 26, 27, 32, 33];
const INAS = ["0x40", "0x41", "0x42", "0x43", "0x44", "0x45"];
const CONFIRM_MS = 10_000;
type Status = "form" | "saving" | "done";
type Out = NonNullable<DeviceHw["out"]>;

const OUT_LABEL: Record<Out, string> = { relay: "Relay", pwm: "MOSFET (PWM)", ir: "IR remote", servo: "Servo", none: "—" };

function Switch({ on, onChange, disabled, label }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button type="button" className={on ? "sw on" : "sw"} aria-pressed={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}>
      <span className="k" />
    </button>
  );
}

function Row({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <div className="sw-row">
      <div className="txt">{title}{sub && <small>{sub}</small>}</div>
      {children}
    </div>
  );
}

export default function AddDevicePage() {
  const toast = useToast();
  const { data: config } = useConfig();
  const { data: nodes } = useNodes();
  const rooms = sortedRooms(config);
  const latestConfig = useRef(config);
  latestConfig.current = config;

  const [tplId, setTplId] = useState("light");
  const tpl = templateById(tplId)!;
  const [roomId, setRoomId] = useState("kitchen");
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [controllable, setControllable] = useState(true);
  const [withLevel, setWithLevel] = useState(false);
  const [control, setControl] = useState<DeviceControl>(tpl.control);
  const [rules, setRules] = useState<DeviceRules>(tpl.rules);
  const [out, setOut] = useState<Out>(tpl.out);
  const [pin, setPin] = useState<number | null>(null);
  const [buttonPin, setButtonPin] = useState<number | null>(null);
  const [ina, setIna] = useState("none");
  const [watts, setWatts] = useState(tpl.watts);
  const [status, setStatus] = useState<Status>("form");
  const waiting = useRef<{ version: number; node: string; id: string; timer: ReturnType<typeof setTimeout> } | null>(null);

  const room = config?.rooms?.[roomId] ?? (rooms[0] ? config?.rooms?.[rooms[0][0]] : undefined);
  const node = room?.node ?? "";

  // pins + INA226 addresses already taken on this node
  const { usedPins, usedInas } = useMemo(() => {
    const pins = new Set<number>(config?.nodes?.[node]?.reserved_pins ?? []);
    const inas = new Set<string>();
    for (const r of Object.values(config?.rooms ?? {})) {
      if (r.node !== node) continue;
      for (const d of Object.values(r.devices ?? {})) {
        if (d.hw?.pin !== undefined) pins.add(d.hw.pin);
        if (d.hw?.button !== undefined) pins.add(d.hw.button);
        if (d.hw?.ina) inas.add(d.hw.ina);
      }
    }
    return { usedPins: pins, usedInas: inas };
  }, [config, node]);

  const freePin = (except?: number | null) => PINS.find((p) => !usedPins.has(p) && p !== except) ?? null;

  // a new template resets the form to its defaults
  useEffect(() => {
    const isWrite = tpl.caps.power === "write";
    setControllable(isWrite);
    setWithLevel(!!tpl.caps.level);
    setControl(tpl.control);
    setRules(tpl.rules);
    setOut(tpl.out);
    setWatts(tpl.watts);
    setIna(tpl.caps.energy === "ina" ? INAS.find((a) => !usedInas.has(a)) ?? "none" : "none");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tplId]);

  useEffect(() => {
    if (!room) return;
    if (!nameEdited) setName(`${room.name} ${tpl.label.toLowerCase()}`);
    if (pin === null || usedPins.has(pin)) setPin(freePin());
    if (buttonPin !== null && usedPins.has(buttonPin)) setButtonPin(null);
    if (ina !== "none" && usedInas.has(ina)) setIna(INAS.find((a) => !usedInas.has(a)) ?? "none");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, tplId, room?.name, usedPins, usedInas]);

  useEffect(() => {
    const w = waiting.current;
    if (w && (nodes?.[w.node]?.config_version ?? 0) >= w.version) {
      clearTimeout(w.timer);
      waiting.current = null;
      setStatus("done");
    }
  }, [nodes]);
  useEffect(() => () => waiting.current?.timer && clearTimeout(waiting.current.timer), []);

  if (!config || !room) return <div className="skeleton" />;

  const monitorOnly = !controllable;
  const effControl: DeviceControl = monitorOnly ? { app: false, button: false, rules: false, ai: false } : control;
  const missingPin = !monitorOnly && out !== "none" && pin === null;
  const missingSensor = monitorOnly && ina === "none";

  function buildDevice(): DeviceConfig {
    const lv = withLevel && !monitorOnly ? tpl.caps.level ?? (tpl.levelOption && { steps: tpl.levelOption.steps, labels: tpl.levelOption.labels }) : undefined;
    const cleanRules: DeviceRules = {};
    if (effControl.rules) {
      if (rules.off_when_empty) cleanRules.off_when_empty = true;
      if (rules.on_when_dark) cleanRules.on_when_dark = true;
      if (rules.follow_temp) cleanRules.follow_temp = true;
    }
    if (monitorOnly && rules.alert_if_off_min) cleanRules.alert_if_off_min = rules.alert_if_off_min;
    const hw: DeviceHw = { out: monitorOnly ? "none" : out };
    if (!monitorOnly && pin !== null) hw.pin = pin;
    if (!monitorOnly && effControl.button && buttonPin !== null) hw.button = buttonPin;
    if (ina !== "none") hw.ina = ina;
    if (monitorOnly) hw.on_above_w = Math.max(0.2, Math.round(watts * 0.15 * 10) / 10);
    return {
      name: name.trim(),
      icon: tpl.icon,
      template: tpl.id,
      caps: {
        power: monitorOnly ? "read" : "write",
        ...(lv ? { level: lv } : {}),
        ...(tpl.caps.mode && !monitorOnly ? { mode: tpl.caps.mode } : {}),
        ...(tpl.caps.status ? { status: true } : {}),
        energy: ina !== "none" ? "ina" : "estimate",
      },
      control: effControl,
      rules: cleanRules,
      hw,
      watts,
      added_at: Date.now(),
    };
  }

  async function save() {
    if (!config || !room || !name.trim() || missingPin || missingSensor) return;
    setStatus("saving");
    const all = Object.values(config.rooms).flatMap((r) => Object.keys(r.devices ?? {}));
    const id = slugId(`${roomId}_${tpl.id}`, (x) => all.includes(x));
    try {
      const version = await addDevice(config, roomId, id, buildDevice());
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

  const hint = monitorOnly
    ? `put the INA226 at ${ina === "none" ? "?" : ina} in series with the ${tpl.label.toLowerCase()} on the ${node} node's I2C bus (GPIO21/22). The home reads whether it is on from the current.`
    : out === "ir"
      ? `connect an IR LED (with a transistor) to GPIO ${pin ?? "?"} on the ${node} node and point it at the ${tpl.label.toLowerCase()}.`
      : `connect the ${out === "relay" ? "relay" : "MOSFET module"} signal to GPIO ${pin ?? "?"} on the ${node} node, share GND with the ESP32, and power the device from its own supply.` +
        (ina !== "none" ? ` INA226 ${ina} goes in series with its supply.` : "");

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
            The {node} node confirmed it.{" "}
            {effControl.ai ? "The AI will start predicting it after about 3 weeks of use." : monitorOnly ? "Its state and energy now appear in the app." : ""}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap", justifyContent: "center" }}>
            <Link href={`/room?id=${roomId}`} className="btn btn-light">Open {room.name}</Link>
            <button type="button" className="btn" onClick={() => { setStatus("form"); setNameEdited(false); setPin(null); }}>Add another</button>
          </div>
        </section>
      ) : (
        <section className="card" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div>
            <div className="h-label">1 · What is it?</div>
            <div className="tpl-grid">
              {TEMPLATES.map((t) => (
                <button key={t.id} type="button" className={tplId === t.id ? "tpl on" : "tpl"} onClick={() => { setTplId(t.id); setNameEdited(false); }}>
                  <span className={`t-ic ic-${t.icon}`}><DeviceIcon type={t.icon} size={18} /></span>
                  {t.label}
                </button>
              ))}
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>{tpl.hint}</div>
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
            <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>Wired to the <b style={{ color: "#F2F3F5" }}>{node}</b> node</div>
          </div>

          <div>
            <div className="h-label">4 · What should the home do with it?</div>
            <div className="seg" role="group" aria-label="Control or monitor">
              <button type="button" className={controllable ? "on" : ""} onClick={() => setControllable(true)}>Control it</button>
              <button type="button" className={!controllable ? "on" : ""} onClick={() => setControllable(false)}>Monitor only</button>
            </div>
            <div style={{ marginTop: 8 }}>
              {controllable && (tpl.levelOption || tpl.caps.level) && (
                <Row title={tpl.levelOption?.label ?? "Level"} sub={(tpl.caps.level?.labels ?? tpl.levelOption?.labels ?? []).join(" / ")}>
                  <Switch on={withLevel} onChange={setWithLevel} label="Level control" />
                </Row>
              )}
              {controllable && tpl.caps.mode && (
                <Row title="Modes" sub={tpl.caps.mode.options.join(" / ")}><span className="muted" style={{ fontSize: 13 }}>Included</span></Row>
              )}
              {!controllable && (
                <Row title="Alert if it stops" sub="For things that should always run, like a fridge">
                  <Switch on={!!rules.alert_if_off_min} onChange={(v) => setRules({ ...rules, alert_if_off_min: v ? 10 : undefined })} label="Alert if it stops" />
                </Row>
              )}
            </div>
          </div>

          {controllable && (
            <div>
              <div className="h-label">5 · Who can change it?</div>
              <Row title="The app"><Switch on={control.app} onChange={(v) => setControl({ ...control, app: v })} label="App" /></Row>
              <Row title="A wall button" sub="A physical button on the node — works without internet">
                <Switch on={control.button} onChange={(v) => { setControl({ ...control, button: v }); if (v && buttonPin === null) setButtonPin(freePin(pin)); }} label="Wall button" />
              </Row>
              <Row title="Automation rules"><Switch on={control.rules} onChange={(v) => setControl({ ...control, rules: v })} label="Rules" /></Row>
              <Row title="The AI" sub="Learns your routine and only ever switches it ON">
                <Switch on={control.ai} onChange={(v) => setControl({ ...control, ai: v })} label="AI" />
              </Row>
              {control.rules && (
                <div style={{ marginTop: 10, padding: "4px 14px", borderRadius: 14, background: "#1C1F24" }}>
                  <Row title="Turn off when the room is empty" sub={`After ${config.thresholds.empty_room_off_min} min`}>
                    <Switch on={!!rules.off_when_empty} onChange={(v) => setRules({ ...rules, off_when_empty: v })} label="Off when empty" />
                  </Row>
                  <Row title="Turn on when dark and someone is there" sub={`Below ${config.thresholds.light_on_lux} lx`}>
                    <Switch on={!!rules.on_when_dark} onChange={(v) => setRules({ ...rules, on_when_dark: v })} label="On when dark" />
                  </Row>
                  <Row title="Follow the room temperature" sub={`On above ${config.thresholds.fan_on_temp}°C, off below ${config.thresholds.fan_off_temp}°C`}>
                    <Switch on={!!rules.follow_temp} onChange={(v) => setRules({ ...rules, follow_temp: v })} label="Follow temperature" />
                  </Row>
                </div>
              )}
            </div>
          )}

          <div>
            <div className="h-label">{controllable ? "6" : "5"} · Wiring</div>
            {controllable && (
              <>
                <div className="scenes" role="group" aria-label="Output type" style={{ flexWrap: "wrap", marginBottom: 10 }}>
                  {(["relay", "pwm", "ir"] as Out[]).map((o) => (
                    <button key={o} type="button" className={out === o ? "scene on" : "scene"} onClick={() => setOut(o)}>{OUT_LABEL[o]}</button>
                  ))}
                </div>
                <div className="muted" style={{ fontSize: 12.5, margin: "4px 0 8px" }}>Output pin</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {PINS.map((p) => {
                    const used = usedPins.has(p) || p === buttonPin;
                    return (
                      <button key={p} type="button" className={pin === p ? "scene on" : "scene"} disabled={used && pin !== p} onClick={() => setPin(p)}
                        style={{ minWidth: 64, justifyContent: "center", opacity: used && pin !== p ? 0.35 : 1 }}>GPIO {p}</button>
                    );
                  })}
                </div>
                {control.button && (
                  <>
                    <div className="muted" style={{ fontSize: 12.5, margin: "12px 0 8px" }}>Wall button pin</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {PINS.map((p) => {
                        const used = usedPins.has(p) || p === pin;
                        return (
                          <button key={p} type="button" className={buttonPin === p ? "scene on" : "scene"} disabled={used && buttonPin !== p} onClick={() => setButtonPin(p)}
                            style={{ minWidth: 64, justifyContent: "center", opacity: used && buttonPin !== p ? 0.35 : 1 }}>GPIO {p}</button>
                        );
                      })}
                    </div>
                  </>
                )}
              </>
            )}
            <div className="muted" style={{ fontSize: 12.5, margin: "12px 0 8px" }}>
              Power sensor (INA226){monitorOnly ? " — required to know if it is on" : " — optional, otherwise estimated"}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {(monitorOnly ? INAS : ["none", ...INAS]).map((a) => {
                const used = usedInas.has(a);
                return (
                  <button key={a} type="button" className={ina === a ? "scene on" : "scene"} disabled={used} onClick={() => setIna(a)}
                    style={{ opacity: used ? 0.35 : 1 }}>{a === "none" ? "None" : a}</button>
                );
              })}
            </div>
            <Row title="Rated power" sub="Used for estimates and for the AI's energy features">
              <input className="num-input num" type="number" min={0} step={0.1} value={watts} onChange={(e) => setWatts(Number(e.target.value) || 0)} aria-label="Rated power in watts" />
            </Row>
          </div>

          <div style={{ borderRadius: 14, background: "#1C1F24", padding: "12px 14px", fontSize: 13.5, color: "#C5CAD1" }}>
            <b style={{ color: "#F2F3F5" }}>Before saving:</b> {hint}
          </div>

          <button type="button" className="btn btn-light btn-block" disabled={status === "saving" || !name.trim() || missingPin || missingSensor} onClick={save}>
            {status === "saving" && <span className="spin" />}
            {status === "saving" ? `Sending to the ${node} node…` : missingSensor ? "Choose a power sensor" : missingPin ? "Choose a pin" : "Save and connect"}
          </button>
        </section>
      )}
    </>
  );
}
