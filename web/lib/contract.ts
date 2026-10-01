// Types for every Firebase path. Mirrors docs/contract.md — change both together.

export type DeviceType = "fan" | "light" | "washer" | "lock";

export interface DeviceConfig {
  type: DeviceType;
  name: string;
  pin: number;
  button?: number;
  ina?: string; // INA226 I2C address, e.g. "0x40"
  watts: number;
}

export interface RoomConfig {
  name: string;
  node: string;
  order: number;
  sensors?: string[];
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
  set: Record<string, SceneValue>;
}

export interface Thresholds {
  fan_on_temp: number;
  light_on_lux: number;
  empty_room_off_min: number;
  ai_act_at: number;
  ai_suggest_at: number;
  override_pause_min: number;
  door_lockout_attempts: number;
}

export interface HomeConfig {
  version: number;
  nodes: Record<string, NodeConfig>;
  rooms: Record<string, RoomConfig>;
  thresholds: Thresholds;
  scenes?: Record<string, SceneConfig>;
}

// ---------------------------------------------------------------- live state

export interface RoomState {
  temp?: number;
  hum?: number;
  lux?: number;
  occ?: 0 | 1;
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

export interface HomeState {
  updated_at: number;
  power_w?: number;
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
  error?: string;
}

// ---------------------------------------------------------------- events log

export type EventKind =
  | "device"
  | "door"
  | "motion"
  | "node"
  | "ai"
  | "rule"
  | "scene"
  | "alert"
  | "config";

export interface HomeEvent {
  id?: string; // push key, added on read
  at: number;
  kind: EventKind;
  device?: string;
  room?: string;
  source: Source;
  by?: string;
  trigger?: string;
  confidence?: number | null;
  from?: Partial<DeviceState> | null;
  to?: Partial<DeviceState> | null;
  result: "ok" | "failed" | "denied";
  latency_ms?: number;
  tags?: string[];
  text: string;
}

// ---------------------------------------------------------------- AI, energy, security

export interface AiDecision {
  device: string;
  p_on: number;
  action: "schedule_on" | "suggest_on" | "suggest_off" | "paused_by_override" | "none";
  predicted_for: string;
  execute_at?: string;
  at: number;
}

export interface Suggestion {
  device: string;
  action: "on" | "off";
  confidence: number;
  text: string;
  at: number;
  response?: "accept" | "dismiss";
}

export type EnergyDay = Record<string, number>; // device -> Wh

export interface AccessEntry {
  at: number;
  method: "fingerprint" | "keypad" | "web" | "exit_button";
  ok: boolean;
  who?: string;
}

export interface Alert {
  at: number;
  level: "critical" | "warning";
  text: string;
  ack?: boolean;
}

export interface UserProfile {
  role: "owner";
  name?: string;
}
