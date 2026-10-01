"use client";

import { useMemo } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "@/components/PageHeader";
import { DEVICE_COLOR } from "@/components/icons";
import { useConfig, useEnergyDaily, useHomeState } from "@/lib/home";
import { dayKey, round } from "@/lib/format";
import type { DeviceConfig } from "@/lib/contract";

export default function EnergyPage() {
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: daily } = useEnergyDaily();

  const devices = useMemo(() => {
    const all: Record<string, DeviceConfig> = {};
    Object.values(config?.rooms ?? {}).forEach((r) => Object.assign(all, r.devices ?? {}));
    return all;
  }, [config]);

  const today = daily?.[dayKey()] ?? {};
  const todayTotal = Object.values(today).reduce((a, b) => a + b, 0);

  const week = useMemo(() => {
    const out = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      const day = daily?.[dayKey(d)] ?? {};
      out.push({
        day: d.toLocaleDateString([], { weekday: "short" }),
        wh: Math.round(Object.values(day).reduce((a, b) => a + b, 0)),
      });
    }
    return out;
  }, [daily]);

  return (
    <>
      <PageHeader title="Energy" sub="Measured by INA226 sensors on each device" />

      <div className="mt-6 grid grid-cols-2 gap-3">
        <div className="card p-4">
          <p className="text-[13px] text-muted">Now</p>
          <p className="mt-1 text-2xl font-bold">{round(state?.power_w, 1)} <span className="text-base text-muted">W</span></p>
        </div>
        <div className="card p-4">
          <p className="text-[13px] text-muted">Today</p>
          <p className="mt-1 text-2xl font-bold">{round(todayTotal, 1)} <span className="text-base text-muted">Wh</span></p>
        </div>
      </div>

      <div className="card mt-4 h-56 p-4">
        <p className="mb-2 text-[13px] text-muted">Last 7 days (Wh)</p>
        <ResponsiveContainer width="100%" height="85%">
          <BarChart data={week}>
            <XAxis dataKey="day" stroke="#9BA1AA" fontSize={12} tickLine={false} axisLine={false} />
            <YAxis stroke="#9BA1AA" fontSize={12} tickLine={false} axisLine={false} width={36} />
            <Tooltip cursor={{ fill: "#212429" }} contentStyle={{ background: "#16181C", border: "1px solid #212429", borderRadius: 12 }} />
            <Bar dataKey="wh" fill="#8FB8FF" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <h2 className="mb-2 mt-8 text-lg font-bold">By device</h2>
      <ul className="card divide-y divide-border px-4">
        {Object.entries(devices)
          .filter(([, d]) => d.type !== "lock")
          .map(([id, d]) => {
            const wh = today[id] ?? 0;
            const pct = todayTotal ? (wh / todayTotal) * 100 : 0;
            return (
              <li key={id} className="py-3">
                <div className="flex justify-between text-sm">
                  <span>{d.name} <span className="text-muted">· {id}</span></span>
                  <span className="text-muted">{round(state?.devices?.[id]?.watts, 1)} W · {round(wh, 1)} Wh</span>
                </div>
                <div className="mt-2 h-1.5 rounded-full bg-tile">
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: DEVICE_COLOR[d.type] }} />
                </div>
              </li>
            );
          })}
      </ul>
    </>
  );
}
