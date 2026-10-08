// Types for every Firebase path. Mirrors docs/contract.md — change both together.

/** Icon / visual family. Free to extend — the UI falls back to a generic icon. */
export type DeviceIconName =
  | "fan" | "light" | "washer" | "lock" | "tv" | "fridge" | "ac" | "heater" | "pump" | "plug" | "generic"
  | "hood" | "vent" | "kettle";

/**
 * What a device CAN do. Every device has at least `power` (control or read) and `energy`.
 *  power  "write" = we can switch it, "read" = we only see whether it is on (monitor only)
 *  level  speed / brightness steps, e.g. [40,70,100] with labels Low/Medium/High
 *  mode   a choice from a list (e.g. air conditioner Cool/Heat/Fan)
 *  status a text state shown on the tile (washer: Running)
 *  lock   momentary unlock (door) — opens for open_s seconds
 *  energy "ina" = measured by an INA226, "estimate" = from rated watts, "none"
 */
export interface DeviceCaps {
  power?: "write" | "read";
  level?: { steps: number[]; labels: string[] };
  mode?: { options: string[] };
  status?: boolean;
  lock?: { open_s: number };
  energy: "ina" | "estimate" | "none";
}

/** WHO may change it. Monitor-only devices have everything false. */
export interface DeviceControl {
  app: boolean; // the web app
  button: boolean; // a wall button on the node
  rules: boolean; // automation rules
  ai: boolean; // the AI model (only ever switches ON)
  confirm?: boolean; // ask "are you sure?" first (door)
}

/** Which automation rules apply (only if control.rules is true). */
export interface DeviceRules {
  off_when_empty?: boolean; // turn off after the room is empty for thresholds.empty_room_off_min
  on_when_dark?: boolean; // turn on when someone is there and light < thresholds.light_on_lux
  follow_temp?: boolean; // on above thresholds.fan_on_temp, off below fan_off_temp
  alert_if_off_min?: number; // monitor: alert if it is off longer than this (fridge)
}

export interface DeviceHw {
  out?: "relay" | "pwm" | "servo" | "ir" | "none"; // how the node drives it
  pin?: number; // output GPIO
  button?: number; // wall button GPIO
  ina?: string; // INA226 I2C address, e.g. "0x40"
  on_above_w?: number; // monitor-only: considered ON above this power
}

export interface DeviceConfig {
  name: string;
  icon: DeviceIconName;
  template?: string; // which template it was created from (informational)
  caps: DeviceCaps;
  control: DeviceControl;
  rules?: DeviceRules;
  hw: DeviceHw;
  watts: number; // rated power, used for estimates
  added_at?: number; // ms — the AI is "learning" a device for ~3 weeks after this
  ai?: DeviceAiSettings; // optional per-device AI overrides (defaults come from caps + watts)
}

/** Optional per-device AI settings. Everything has a default (see ai/spec.py and ai/energy.py). */
export interface DeviceAiSettings {
  act?: number; // act threshold; default = energy-scaled from watts (1.2 W light ~0.68, 1.5 kW AC ~0.92)
  lead_min?: number; // start early (pre-cooling); default 15 for fans/AC/heaters, 0 otherwise
  off_after_min?: number; // smart off after confirmed vacancy; default lights 5, others 10
  grace_min?: number; // waste guard: switch off if nobody came by target + this; default 10
  temp_on?: number; // thermal gate; default thresholds.fan_on_temp
}

/** @deprecated kept for older code paths; use DeviceConfig.icon */
export type DeviceType = DeviceIconName;

export interface RoomConfig {
  name: string;
  node: string;
  order: number;
  hidden?: boolean; // not shown as a room on Home (e.g. the entrance / door)
  sensors?: string[]; // live values: temp, hum, lux, occ
  hardware?: string[]; // sensor modules, for the room screen
  windowless?: boolean; // no daylight (e.g. a bathroom): lights are needed whenever someone is there
  devices?: Record<string, DeviceConfig>;
}

export interface NodeConfig {
  name: string;
  reserved_pins: number[];
  i2c: string[];
}

export type SceneValue = number | { v: number; level?: number; mode?: string };

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
  /** finger id -> name. Firebase may return it as an array ([null, "Owner", "Fares"]) because the keys are numbers. */
  fingerprints?: Record<string, string> | (string | null)[];
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
  mmwave?: 0 | 1; // each presence sensor separately: switching OFF needs both to be empty
  pir?: 0 | 1;
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
  level?: number;
  mode?: string;
  status?: string;
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
  level?: number;
  mode?: string;
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
  action: "schedule_on" | "keep_on" | "suggest_on" | "suggest_off" | "paused_by_override" | "paused_by_user" | "learning" | "none";
  status?: "ready" | "relearning" | "learning";
  predicted_for: string;
  time?: string; // "19:30" — when it will act (lights: from when it switches on as you walk in)
  title?: string;
  why?: string; // built from the real sensor values, e.g. "Room 29.4°C · someone is home · 86% sure"
  execute_at?: string;
  act_at?: number; // the threshold used for this device right now (energy-scaled + adaptive)
  suggest_at?: number;
  at: number;
}

export interface AiDeviceInsight {
  status: "ready" | "relearning" | "learning";
  days?: number;
  f1?: number;
  baseline_f1?: number; // "same as yesterday" — the model must beat this
  within_15?: number;
  exact?: number;
  brier?: number;
  importance?: Record<string, number>; // feature group -> share, e.g. { time: 0.41, habit: 0.33 }
  calibration?: [number, number, number][]; // [predicted, actual, count] per bin
  act: number;
  suggest: number;
  watts: number;
  kind: "light" | "thermal" | "generic";
}

export interface EnergyAnomaly {
  device: string;
  kind: "high" | "dead" | "standby";
  watts: number;
  usual: number;
  at: number;
  title?: string;
  text?: string;
}

export interface AiInsights {
  status?: "ready" | "learning" | "off";
  data_source?: "simulated" | "mixed" | "real"; // say so in the UI when the numbers come from simulated data
  metrics?: {
    within_15: number;
    exact: number;
    retrained_at: number;
    model?: string;
    devices?: number;
    f1?: number;
    baseline_f1?: number;
    data_source?: "simulated" | "mixed" | "real";
  };
  learned?: string[]; // **bold** marks numbers
  devices?: Record<string, AiDeviceInsight>;
  presence?: { status: string; accuracy?: number; arrival_weekday?: string | null; arrival_weekend?: string | null };
  presence_now?: { home: boolean; p_home_60: number; at: number };
  energy?: { wasted_wh: number; saved_by_ai_wh: number; saved_by_rules_wh: number; wasted_by_ai_wh: number; ai_net_wh: number };
  anomalies?: EnergyAnomaly[];
  wear?: Record<string, number>; // device -> rise of its usual power over 4 weeks (0.3 = +30%)
  stats?: Record<string, number>; // ai_on, ai_hit, ai_miss, smart_off, suggested, accepted, dismissed, expired, skipped,
  // ai_entry_on, ai_entry_skip, ai_auto_off, ai_trusted, ai_undone
  trust?: AiTrust[]; // trust ladder (2026-10-09): what the AI learned to do without asking
}

export interface AiTrust {
  device: string;
  label: string;
  kind: "off_still" | "suggest_on";
  level: 0 | 1 | 2; // 0 asks first · 1 does it and tells you · 2 does it by itself
  text: string;
  yes: number; // "yes" answers in a row (level 0 -> 1 after 3)
  ok: number; // automatic actions nobody undid (level 1 -> 2 after 5)
}

export interface Suggestion {
  device: string;
  action: "on" | "off";
  confidence: number;
  title: string;
  why: string;
  at: number;
  response?: "accept" | "dismiss";
  expires_at?: number; // the Pi deletes the suggestion after this (or when the room changes)
  handled?: boolean;
  done_text?: string;
}

export type EnergyDay = Record<string, number>; // device -> Wh, "_base" = hub + nodes + sensors

/** /energy_waste/{day}/{device}/{cause} = Wh used while the room was confirmed empty */
export type WasteCause = "forgotten" | "rule_delay" | "ai" | "standby";
export type EnergyWasteDay = Record<string, Partial<Record<WasteCause, number>>>;

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
