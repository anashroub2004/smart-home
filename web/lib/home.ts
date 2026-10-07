"use client";

// The ONLY file that knows Firebase paths. Screens import hooks from here, never `firebase/database`.
// When the data source changes (simulator → Pi → real ESP32) nothing in this file changes.

import {
  increment,
  limitToLast,
  onValue,
  orderByChild,
  query,
  ref,
  serverTimestamp,
  set,
  update,
  type Query,
} from "firebase/database";
import { useCallback, useEffect, useRef, useState } from "react";
import { fbAuth, fbDb } from "./firebase";
import type {
  AccessEntry,
  AiDecision,
  AiInsights,
  Alert,
  Command,
  DeviceConfig,
  EnergyDay,
  EnergyWasteDay,
  HomeConfig,
  HomeEvent,
  HomeState,
  NodeStatus,
  Prefs,
  RoomConfig,
  SceneValue,
  Suggestion,
  Summary,
  Thresholds,
} from "./contract";

export const PATHS = {
  config: "config",
  homeState: "home_state",
  nodes: "nodes",
  command: (device: string) => `commands/${device}`,
  events: "events",
  summaries: (room: string, day: string) => `summaries/${room}/${day}`,
  energyDaily: "energy_daily",
  energyWaste: "energy_waste",
  aiSchedule: "ai_schedule",
  aiInsights: "ai_insights",
  aiPause: (device: string) => `ai_pause/${device}`,
  suggestions: "suggestions",
  suggestion: (id: string) => `suggestions/${id}`,
  accessLog: "access_log",
  alerts: "alerts",
  alertAck: (id: string) => `alerts/${id}/ack`,
  prefs: "prefs",
} as const;

// ---------------------------------------------------------------- generic subscription

export interface Live<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

function useQuery<T>(make: (() => Query) | null, deps: unknown[]): Live<T> {
  const [state, setState] = useState<Live<T>>({ data: null, loading: true, error: null });
  useEffect(() => {
    if (!make) return;
    return onValue(
      make(),
      (snap) => setState({ data: snap.val() as T | null, loading: false, error: null }),
      (err) => setState({ data: null, loading: false, error: err.message }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function useValue<T>(path: string | null): Live<T> {
  return useQuery<T>(path ? () => ref(fbDb(), path) : null, [path]);
}

/** Firebase object of push-keys → newest-first array with `id`. */
function toList<T extends { at: number }>(obj: Record<string, T> | null): (T & { id: string })[] {
  if (!obj) return [];
  return Object.entries(obj)
    .map(([id, v]) => ({ ...v, id }))
    .sort((a, b) => b.at - a.at);
}

function useLatest<T extends { at: number }>(path: string, limit: number) {
  const live = useQuery<Record<string, T>>(
    () => query(ref(fbDb(), path), orderByChild("at"), limitToLast(limit)),
    [path, limit],
  );
  return { ...live, list: toList(live.data) };
}

// ---------------------------------------------------------------- typed hooks

export const useConfig = () => useValue<HomeConfig>(PATHS.config);
export const useHomeState = () => useValue<HomeState>(PATHS.homeState);
export const useNodes = () => useValue<Record<string, NodeStatus>>(PATHS.nodes);
export const useEnergyDaily = () => useValue<Record<string, EnergyDay>>(PATHS.energyDaily);
/** Wasted Wh per device and cause for one day ("YYYY-MM-DD"). */
export const useEnergyWaste = (day: string) => useValue<EnergyWasteDay>(`${PATHS.energyWaste}/${day}`);
export const useAiSchedule = () => useValue<Record<string, AiDecision>>(PATHS.aiSchedule);
export const useAiInsights = () => useValue<AiInsights>(PATHS.aiInsights);
export const usePrefs = () => useValue<Prefs>(PATHS.prefs);
/** One room's per-minute summaries for one day ("YYYY-MM-DD"), keyed "HH:MM". */
export const useSummaries = (room: string | null, day: string) =>
  useValue<Record<string, Summary>>(room ? PATHS.summaries(room, day) : null);
/** ms timestamp until which automation (AI + rules) leaves this device alone; null = not paused. */
export const useAiPause = (device: string) => useValue<number>(PATHS.aiPause(device));

export const useEvents = (limit = 100) => useLatest<HomeEvent>(PATHS.events, limit);
export const useAccessLog = (limit = 30) => useLatest<AccessEntry>(PATHS.accessLog, limit);
export const useAlerts = (limit = 30) => useLatest<Alert>(PATHS.alerts, limit);

export function useSuggestions() {
  const live = useValue<Record<string, Suggestion>>(PATHS.suggestions);
  return { ...live, list: toList(live.data) };
}

/** true while the browser is connected to Firebase. */
export function useConnected(): boolean {
  const { data } = useValue<boolean>(".info/connected");
  return data === true;
}

// ---------------------------------------------------------------- writes

const uid = () => fbAuth().currentUser?.uid ?? "unknown";

export interface CommandOpts {
  level?: number;
  mode?: string;
}

export async function sendCommand(
  device: string,
  v: 0 | 1,
  opts: CommandOpts = {},
  extra?: Pick<Command, "scene" | "via" | "confidence">,
) {
  const cmd: Command = { v, by: uid(), at: serverTimestamp(), status: "pending" };
  if (opts.level !== undefined) cmd.level = opts.level; // Firebase rejects `undefined`
  if (opts.mode !== undefined) cmd.mode = opts.mode;
  if (extra?.scene) cmd.scene = extra.scene;
  if (extra?.via) cmd.via = extra.via;
  if (extra?.confidence !== undefined) cmd.confidence = extra.confidence;
  await set(ref(fbDb(), PATHS.command(device)), cmd);
}

export async function applyScene(sceneId: string, setMap: Record<string, SceneValue>) {
  await Promise.all(
    Object.entries(setMap).map(([device, val]) =>
      typeof val === "number"
        ? sendCommand(device, val as 0 | 1, {}, { scene: sceneId })
        : sendCommand(device, val.v as 0 | 1, { level: val.level, mode: val.mode }, { scene: sceneId }),
    ),
  );
}

/** Returns false when the suggestion was already withdrawn by the hub (expired or the room changed). */
export async function answerSuggestion(id: string, s: Suggestion, accept: boolean): Promise<boolean> {
  try {
    await update(ref(fbDb(), PATHS.suggestion(id)), { response: accept ? "accept" : "dismiss" });
  } catch {
    return false; // the rules only allow answering a suggestion that still exists
  }
  if (accept) {
    await sendCommand(s.device, s.action === "on" ? 1 : 0, {}, {
      via: "suggestion",
      confidence: s.confidence,
    });
  }
  return true;
}

/** minutes = null resumes automation now. */
export async function setAiPause(device: string, minutes: number | null) {
  await set(ref(fbDb(), PATHS.aiPause(device)), minutes ? Date.now() + minutes * 60_000 : null);
}

export async function ackAlert(id: string) {
  await set(ref(fbDb(), PATHS.alertAck(id)), true);
}

export async function setPref(path: "notify/waste" | "notify/ai", value: boolean) {
  await set(ref(fbDb(), `${PATHS.prefs}/${path}`), value);
}

/** Every config change bumps `version`; nodes confirm it in /nodes/{node}/config_version. */
export async function saveConfig(config: HomeConfig): Promise<number> {
  const version = config.version + 1;
  await set(ref(fbDb(), PATHS.config), { ...config, version });
  return version;
}

/** Multi-path update so quick taps on a stepper never overwrite each other. */
export async function setThreshold(key: keyof Thresholds, value: number) {
  await update(ref(fbDb(), PATHS.config), { [`thresholds/${key}`]: value, version: increment(1) });
}

export async function addDevice(config: HomeConfig, room: string, id: string, device: DeviceConfig) {
  const r = config.rooms[room];
  return saveConfig({
    ...config,
    rooms: { ...config.rooms, [room]: { ...r, devices: { ...(r.devices ?? {}), [id]: device } } },
  });
}

export async function addRoom(config: HomeConfig, id: string, room: RoomConfig) {
  return saveConfig({ ...config, rooms: { ...config.rooms, [id]: room } });
}

export async function updateDevice(config: HomeConfig, room: string, id: string, device: DeviceConfig) {
  return addDevice(config, room, id, device);
}

export async function removeDevice(config: HomeConfig, room: string, id: string) {
  const r = config.rooms[room];
  const devices = { ...(r.devices ?? {}) };
  delete devices[id];
  return saveConfig({ ...config, rooms: { ...config.rooms, [room]: { ...r, devices } } });
}

// ---------------------------------------------------------------- command lifecycle for one control

export type CmdUi = "idle" | "pending" | "done" | "failed" | "timeout";
const TIMEOUT_MS = 10_000;

export function useDeviceCommand(device: string, onFail?: (why: CmdUi, error?: string) => void) {
  const [ui, setUi] = useState<CmdUi>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const waiting = useRef(false);
  const failCb = useRef(onFail);
  failCb.current = onFail;
  const { data } = useValue<Command>(PATHS.command(device));

  useEffect(() => {
    if (!waiting.current || !data) return;
    if (data.status === "done" || data.status === "failed") {
      waiting.current = false;
      if (timer.current) clearTimeout(timer.current);
      setUi(data.status);
      if (data.status === "failed") failCb.current?.("failed", data.error);
      const t = setTimeout(() => setUi("idle"), data.status === "done" ? 300 : 2500);
      return () => clearTimeout(t);
    }
  }, [data]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const send = useCallback(
    async (v: 0 | 1, opts: CommandOpts = {}) => {
      waiting.current = true;
      setUi("pending");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        if (waiting.current) {
          waiting.current = false;
          setUi("timeout");
          failCb.current?.("timeout");
          setTimeout(() => setUi("idle"), 2500);
        }
      }, TIMEOUT_MS);
      try {
        await sendCommand(device, v, opts);
      } catch {
        waiting.current = false;
        setUi("failed");
        failCb.current?.("failed");
      }
    },
    [device],
  );

  return { ui, error: data?.error, send };
}
