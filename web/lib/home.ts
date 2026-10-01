"use client";

// The ONLY file that knows Firebase paths. Screens import hooks from here, never `firebase/database`.
// When the data source changes (simulator → Pi → real ESP32) nothing in this file changes.

import {
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
  Alert,
  Command,
  EnergyDay,
  HomeConfig,
  HomeEvent,
  HomeState,
  NodeStatus,
  SceneValue,
  Suggestion,
} from "./contract";

export const PATHS = {
  config: "config",
  homeState: "home_state",
  nodes: "nodes",
  command: (device: string) => `commands/${device}`,
  events: "events",
  energyDaily: "energy_daily",
  aiSchedule: "ai_schedule",
  aiPause: (device: string) => `ai_pause/${device}`,
  suggestions: "suggestions",
  suggestion: (id: string) => `suggestions/${id}`,
  accessLog: "access_log",
  alerts: "alerts",
  alertAck: (id: string) => `alerts/${id}/ack`,
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
function toList<T>(obj: Record<string, T> | null, key = "at"): (T & { id: string })[] {
  if (!obj) return [];
  return Object.entries(obj)
    .map(([id, v]) => ({ ...v, id }))
    .sort((a, b) => Number((b as Record<string, unknown>)[key]) - Number((a as Record<string, unknown>)[key]));
}

function useLatest<T>(path: string, limit: number) {
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
export const useAiSchedule = () => useValue<Record<string, AiDecision>>(PATHS.aiSchedule);
/** ms timestamp until which automation (AI + rules) leaves this device alone; null = not paused. */
export const useAiPause = (device: string) => useValue<number>(PATHS.aiPause(device));

export const useEvents = (limit = 100) => useLatest<HomeEvent>(PATHS.events, limit);
export const useAccessLog = (limit = 30) => useLatest<AccessEntry>(PATHS.accessLog, limit);
export const useAlerts = (limit = 20) => useLatest<Alert>(PATHS.alerts, limit);

export function useSuggestions() {
  const live = useValue<Record<string, Suggestion>>(PATHS.suggestions);
  return { ...live, list: toList(live.data).filter((s) => !s.response) };
}

/** true while the browser is connected to Firebase. */
export function useConnected(): boolean {
  const { data } = useValue<boolean>(".info/connected");
  return data === true;
}

// ---------------------------------------------------------------- writes

export async function sendCommand(device: string, v: 0 | 1, speed?: number, scene?: string) {
  const cmd: Command = {
    v,
    by: fbAuth().currentUser?.uid ?? "unknown",
    at: serverTimestamp(),
    status: "pending",
  };
  if (speed !== undefined) cmd.speed = speed; // Firebase rejects `undefined`
  if (scene) cmd.scene = scene;
  await set(ref(fbDb(), PATHS.command(device)), cmd);
}

export async function applyScene(sceneId: string, setMap: Record<string, SceneValue>) {
  await Promise.all(
    Object.entries(setMap).map(([device, val]) =>
      typeof val === "number"
        ? sendCommand(device, val as 0 | 1, undefined, sceneId)
        : sendCommand(device, val.v as 0 | 1, val.speed, sceneId),
    ),
  );
}

export async function answerSuggestion(id: string, s: Suggestion, accept: boolean) {
  await update(ref(fbDb(), PATHS.suggestion(id)), { response: accept ? "accept" : "dismiss" });
  if (accept) await sendCommand(s.device, s.action === "on" ? 1 : 0);
}

/** minutes = null resumes automation now. */
export async function setAiPause(device: string, minutes: number | null) {
  await set(ref(fbDb(), PATHS.aiPause(device)), minutes ? Date.now() + minutes * 60_000 : null);
}

export async function ackAlert(id: string) {
  await set(ref(fbDb(), PATHS.alertAck(id)), true);
}

export async function saveConfig(config: HomeConfig) {
  await set(ref(fbDb(), PATHS.config), { ...config, version: config.version + 1 });
}

// ---------------------------------------------------------------- command lifecycle for one tile

export type CmdUi = "idle" | "pending" | "done" | "failed" | "timeout";
const TIMEOUT_MS = 10_000;

export function useDeviceCommand(device: string) {
  const [ui, setUi] = useState<CmdUi>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const waiting = useRef(false);
  const { data } = useValue<Command>(PATHS.command(device));

  useEffect(() => {
    if (!waiting.current || !data) return;
    if (data.status === "done" || data.status === "failed") {
      waiting.current = false;
      if (timer.current) clearTimeout(timer.current);
      setUi(data.status);
      const t = setTimeout(() => setUi("idle"), data.status === "done" ? 800 : 4000);
      return () => clearTimeout(t);
    }
  }, [data]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const send = useCallback(
    async (v: 0 | 1, speed?: number) => {
      waiting.current = true;
      setUi("pending");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        if (waiting.current) {
          waiting.current = false;
          setUi("timeout");
        }
      }, TIMEOUT_MS);
      try {
        await sendCommand(device, v, speed);
      } catch {
        waiting.current = false;
        setUi("failed");
      }
    },
    [device],
  );

  return { ui, error: data?.error, send };
}
