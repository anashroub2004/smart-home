// Turns contract data into the words and colours the design uses. No Firebase here.

import type { Alert, DeviceConfig, DeviceState, EventGroup, HomeConfig, RoomConfig } from "./contract";

// ---------------------------------------------------------------- capabilities

export const canSwitch = (cfg: DeviceConfig) => cfg.caps.power === "write" || !!cfg.caps.lock;
/** Can the person tap it in the app? (monitor-only devices and app:false devices can't) */
export const appControllable = (cfg: DeviceConfig) => canSwitch(cfg) && cfg.control.app;
export const isMonitorOnly = (cfg: DeviceConfig) => !cfg.caps.lock && cfg.caps.power !== "write";
export const isLock = (cfg: DeviceConfig) => !!cfg.caps.lock;

/** Label for a level value, using the device's own steps ("Medium", "Bright", "50%"). */
export function levelLabel(cfg: DeviceConfig, value?: number): string | null {
  const lv = cfg.caps.level;
  if (!lv) return null;
  const v = value ?? lv.steps[Math.floor(lv.steps.length / 2)];
  let best = 0;
  lv.steps.forEach((s, i) => {
    if (Math.abs(s - v) < Math.abs(lv.steps[best] - v)) best = i;
  });
  return lv.labels[best] ?? `${v}%`;
}

export const defaultLevel = (cfg: DeviceConfig) => {
  const s = cfg.caps.level?.steps;
  return s ? s[Math.floor(s.length / 2)] : undefined;
};

export function deviceWatts(cfg: DeviceConfig, st?: DeviceState): number {
  if (!st?.v) return 0;
  if (st.watts !== undefined) return st.watts;
  return cfg.caps.level ? (cfg.watts * (st.level ?? 70)) / 100 : cfg.watts;
}

/** Text under the device name on a tile — same wording as the prototype. */
export function tileState(cfg: DeviceConfig, st: DeviceState | undefined, offline: boolean, pending: boolean): string {
  if (offline) return "Not responding";
  if (pending) return "Updating…";
  if (isLock(cfg)) return st?.v ? "Unlocked" : "Locked";
  if (!st?.v) return "Off";
  const w = cfg.caps.energy === "none" ? "" : ` · ${deviceWatts(cfg, st).toFixed(1)} W`;
  if (cfg.caps.level) return `${levelLabel(cfg, st.level)}${w}`;
  if (cfg.caps.mode && st.mode) return `${st.mode}${w}`;
  if (cfg.caps.status) return `${st.status ?? "Running"}${w}`;
  return `On${w}`;
}

/** "App · Button · Rules · AI" or "Monitor only" */
export function controlSummary(cfg: DeviceConfig): string {
  if (isMonitorOnly(cfg)) return "Monitor only";
  const c = cfg.control;
  const parts = [c.app && "App", c.button && "Button", c.rules && "Rules", c.ai && "AI"].filter(Boolean);
  return parts.length ? parts.join(" · ") : "No one";
}

// ---------------------------------------------------------------- lookup

export interface DeviceRef {
  id: string;
  cfg: DeviceConfig;
  roomId: string;
  room: RoomConfig;
  node: string;
  /** "Living room fan", "Washer" */
  label: string;
}

export function deviceLabel(cfg: DeviceConfig, room: RoomConfig): string {
  if (cfg.icon === "washer" || cfg.icon === "lock" || cfg.name.toLowerCase().startsWith(room.name.toLowerCase())) return cfg.name;
  const n = cfg.name === cfg.name.toUpperCase() ? cfg.name : cfg.name.toLowerCase(); // keep "TV", "AC"
  return `${room.name} ${n}`;
}

export function findDevice(config: HomeConfig | null, id: string): DeviceRef | null {
  for (const [roomId, room] of Object.entries(config?.rooms ?? {})) {
    const cfg = room.devices?.[id];
    if (cfg) return { id, cfg, roomId, room, node: room.node, label: deviceLabel(cfg, room) };
  }
  return null;
}

export function allDevices(config: HomeConfig | null): DeviceRef[] {
  const out: DeviceRef[] = [];
  for (const [roomId, room] of sortedRooms(config, true)) {
    for (const [id, cfg] of Object.entries(room.devices ?? {})) {
      out.push({ id, cfg, roomId, room, node: room.node, label: deviceLabel(cfg, room) });
    }
  }
  return out;
}

/** Rooms in display order. Hidden rooms (the entrance) are left out unless asked for. */
export function sortedRooms(config: HomeConfig | null, includeHidden = false): [string, RoomConfig][] {
  return Object.entries(config?.rooms ?? {})
    .filter(([, r]) => includeHidden || !r.hidden)
    .sort(([, a], [, b]) => a.order - b.order);
}

export const hasClimate = (r: RoomConfig) => (r.sensors ?? []).includes("temp");

/** "kitchen_light", "kitchen_light_2", … */
export function slugId(base: string, taken: (id: string) => boolean): string {
  const clean = base.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "device";
  let id = clean;
  for (let n = 2; taken(id); n++) id = `${clean}_${n}`;
  return id;
}

// ---------------------------------------------------------------- colours (prototype values)

export const GROUP_COLOR: Record<EventGroup, string> = {
  manual: "#8FB8FF",
  ai: "#C9A7FF",
  rule: "#5FD3C4",
  door: "#FFB020",
  system: "#C5CAD1",
};
export const GROUP_BG: Record<EventGroup, string> = {
  manual: "#1A2436",
  ai: "#241C33",
  rule: "#13292A",
  door: "#2A2310",
  system: "#1F2227",
};
export const ALERT_COLOR: Record<Alert["level"], string> = {
  critical: "#FF5A52",
  warning: "#FFB020",
  info: "#8FB8FF",
  good: "#34C759",
};

export const C = {
  good: "#34C759",
  warn: "#FFB020",
  bad: "#FF5A52",
  accent: "#8FB8FF",
  muted: "#9BA1AA",
  dim: "#6E747D",
};

// ---------------------------------------------------------------- time words

export function greeting(d = new Date()): string {
  const h = d.getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function dateLine(d = new Date()): string {
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
}

export function isToday(ms: number): boolean {
  const d = new Date(ms);
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

export function minutesText(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} m`;
}
