// Types for every Firebase path. Mirrors docs/contract.md — change both together.

export type DeviceType = "fan" | "light" | "washer" | "lock";

export interface DeviceConfig {
  type: DeviceType;
  name: string;
  pin: number;
  button?: number;
  ina?: string; // INA226 I2C address, e.g. "0x40"
  watts: number;
  added_at?: number; // ms — the AI is "learning" a device for ~3 weeks after this
}

export interface RoomConfig {
  name: string;
  node: string;
  order: number;
  hidden?: boolean; // not shown as a room on Home (e.g. the entrance / door)
  sensors?: string[]; // live values: temp, hum, lux, occ
  hardware?: string[]; // sensor modules, for the room screen
  devices?: Record<string, DeviceConfig>;
}

export interface NodeConfig {
  name: string;
  reserved_pins: number[];
  i2c: string[];
}

export type SceneValue = number | { v: number; speed?: number };

export interface SceneConfig {
  name: string;
  order?: number;
  set: Record<string, SceneValue>;
}

export interface Thresholds {
  fan_on_temp: number;
  fan_off_temp: number;
  light_on_lux: number;
  empty_room_off_min: number;
  ai_act_at: number;
  ai_suggest_at: number;
  override_pause_min: number;
  door_lockout_attempts: number;
  door_lockout_s?: number;
}

export interface HomeConfig {
  schema?: number;
  version: number;
  home_name?: string;
  nodes: Record<string, NodeConfig>;
  rooms: Record<string, RoomConfig>;
  thresholds: Thresholds;
  scenes?: Record<string, SceneConfig>;
  fingerprints?: Record<string, string>;
}

// ---------------------------------------------------------------- live state

export interface RoomState {
  temp?: number;
  hum?: number;
  lux?: number;
  occ?: 0 | 1;
  occ_since?: number;
  occ_by?: "mmwave" | "pir";
  motion_at?: number;
}

export type Source =
  | "web"
  | "button"
  | "ai"
  | "rule"
  | "keypad"
  | "fingerprint"
  | "exit_button"
  | "system";

export interface DeviceState {
  v: 0 | 1;
  speed?: number;
  src?: Source;
  at?: number;
  watts?: number;
}

export interface DoorState {
  lockout_until?: number;
  last_open?: { at: number; method: AccessEntry["method"]; who?: string };
}

export interface HomeState {
  updated_at: number;
  power_w?: number;
  base_w?: number;
  scene?: string;
  door?: DoorState;
  rooms?: Record<string, RoomState>;
  devices?: Record<string, DeviceState>;
}

export interface NodeStatus {
  online: boolean;
  last_seen: number;
  config_version?: number;
  rssi?: number;
}

// ---------------------------------------------------------------- commands

export type CommandStatus = "pending" | "done" | "failed";

export interface Command {
  v: 0 | 1;
  speed?: number;
  by: string;
  at: number | object; // object = serverTimestamp() placeholder when writing
  status: CommandStatus;
  scene?: string;
  via?: "suggestion";
  confidence?: number;
  error?: string;
}

// ---------------------------------------------------------------- events log (written by the Pi only)

export type EventKind = "device" | "door" | "motion" | "node" | "ai" | "rule" | "scene" | "alert" | "config";
export type EventGroup = "manual" | "ai" | "rule" | "door" | "system";

export interface HomeEvent {
  id?: string; // push key, added on read
  at: number;
  kind: EventKind;
  group: EventGroup;
  title: string; // "Living room fan turned on"
  short?: string; // "Pre-cooling before you got home"
  source: Source;
  src_label: string; // "App", "AI · 86%", "Rule", "Scene · Away", "Fingerprint"
  by?: string; // uid
  by_label?: string; // "Owner (web app)"
  why?: string;
  change?: string; // "Off → On · Medium"
  device?: string;
  room?: string;
  node?: string;
  confidence?: number;
  from?: Partial<DeviceState>;
  to?: Partial<DeviceState>;
  result: "ok" | "failed" | "denied";
  latency_ms?: number;
  saved_wh?: number;
  tags?: string[];
}

// ---------------------------------------------------------------- AI, energy, security

export interface AiDecision {
  device: string;
  p_on: number;
  action: "schedule_on" | "keep_on" | "suggest_on" | "suggest_off" | "paused_by_override" | "none";
  predicted_for: string;
  time?: string; // "19:30"
  title?: string;
  why?: string;
  execute_at?: string;
  at: number;
}

export interface AiInsights {
  metrics?: { within_15: number; exact: number; retrained_at: number; model?: string; devices?: number };
  learned?: string[]; // **bold** marks numbers
}

export interface Suggestion {
  device: string;
  action: "on" | "off";
  confidence: number;
  title: string;
  why: string;
  at: number;
  response?: "accept" | "dismiss";
  handled?: boolean;
  done_text?: string;
}

export type EnergyDay = Record<string, number>; // device -> Wh, "_base" = hub + nodes + sensors

export interface AccessEntry {
  at: number;
  method: "fingerprint" | "keypad" | "web" | "exit_button";
  ok: boolean;
  who?: string;
}

export interface Alert {
  at: number;
  level: "critical" | "warning" | "info" | "good";
  title: string;
  where: string;
  go: "security" | "energy" | "settings" | "insights";
  lines?: string[]; // details for the full-screen critical alert
  ack?: boolean;
}

export interface Prefs {
  notify?: { waste?: boolean; ai?: boolean };
}

export interface Summary {
  temp?: number;
  hum?: number;
  lux?: number;
  occ?: 0 | 1;
  watts?: number;
  dev?: Record<string, 0 | 1>;
}

export interface UserProfile {
  role: "owner";
  name?: string;
}
