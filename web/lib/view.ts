// Turns contract data into the words and colours the design uses. No Firebase here.

import type { Alert, DeviceConfig, DeviceState, EventGroup, HomeConfig, RoomConfig } from "./contract";

export const SPEEDS = { Low: 40, Medium: 70, High: 100 } as const;

export function speedName(v?: number): keyof typeof SPEEDS {
  const s = v ?? 70;
  return s >= 100 ? "High" : s >= 70 ? "Medium" : "Low";
}

export function deviceWatts(cfg: DeviceConfig, st?: DeviceState): number {
  if (!st?.v) return 0;
  if (st.watts !== undefined) return st.watts;
  return cfg.type === "fan" ? (cfg.watts * (st.speed ?? 70)) / 100 : cfg.watts;
}

/** Text under the device name on a tile — same wording as the prototype. */
export function tileState(cfg: DeviceConfig, st: DeviceState | undefined, offline: boolean, pending: boolean): string {
  if (offline) return "Not responding";
  if (pending) return "Updating…";
  if (cfg.type === "lock") return st?.v ? "Unlocked" : "Locked";
  if (!st?.v) return "Off";
  const w = deviceWatts(cfg, st).toFixed(1) + " W";
  if (cfg.type === "fan") return `${speedName(st.speed)} · ${w}`;
  if (cfg.type === "washer") return `Running · ${w}`;
  return `On · ${w}`;
}

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
  return cfg.type === "washer" || cfg.type === "lock" ? cfg.name : `${room.name} ${cfg.name.toLowerCase()}`;
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
