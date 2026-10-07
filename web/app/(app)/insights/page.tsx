"use client";

import { useState } from "react";
import { Md } from "@/components/Md";
import { answerSuggestion, useAiInsights, useAiSchedule, useConfig, useSuggestions } from "@/lib/home";
import { clock } from "@/lib/format";
import { aiStatusText, allDevices, C, isToday, pct, topDrivers } from "@/lib/view";

const wh = (x?: number) => (x === undefined ? "—" : `${x < 10 ? x.toFixed(1) : Math.round(x)} Wh`);

export default function InsightsPage() {
  const { data: config } = useConfig();
  const { data: schedule } = useAiSchedule();
  const { list: suggestions } = useSuggestions();
  const { data: insights } = useAiInsights();
  const [expired, setExpired] = useState<Record<string, boolean>>({});

  const actAt = Math.round((config?.thresholds.ai_act_at ?? 0.8) * 100);
  const pauseH = (config?.thresholds.override_pause_min ?? 120) / 60;
  const plan = Object.values(schedule ?? {})
    .filter((d) => (d.action === "schedule_on" || d.action === "keep_on") && d.title)
    .sort((a, b) => (a.time ?? "").localeCompare(b.time ?? ""));
  const sugg = suggestions.filter((s) => isToday(s.at)).slice(0, 6);
  const m = insights?.metrics;
  const simulated = insights?.data_source && insights.data_source !== "real";
  const off = insights?.status === "off";
  const energy = insights?.energy;
  const stats = insights?.stats ?? {};
  const aiOns = (stats.ai_hit ?? 0) + (stats.ai_miss ?? 0);
  const answered = (stats.accepted ?? 0) + (stats.dismissed ?? 0);
  const faults = [...(insights?.anomalies ?? [])].reverse().slice(0, 5);
  const wear = Object.entries(insights?.wear ?? {});
  const labels = Object.fromEntries(allDevices(config).map((d) => [d.id, d.label]));
  const devices = Object.entries(insights?.devices ?? {});
  const presence = insights?.presence;
  const now = insights?.presence_now;

  const answer = async (id: string, s: (typeof sugg)[number], yes: boolean) => {
    if (!(await answerSuggestion(id, s, yes))) setExpired((e) => ({ ...e, [id]: true }));
  };

  return (
    <>
      <h1 style={{ margin: 0, fontSize: 28, fontWeight: 700 }}>Insights</h1>
      <p className="muted" style={{ margin: "-12px 0 0", fontSize: 14 }}>
        Your home learns your routine and prepares rooms before you need them.
      </p>
      {(simulated || insights?.status) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: -8 }}>
          {insights?.status && (
            <span className="tag" style={{ background: "#1F2227", color: off ? C.muted : insights.status === "ready" ? C.good : C.warn }}>
              {off ? "AI is off" : insights.status === "ready" ? "AI active" : "AI learning"}
            </span>
          )}
          {simulated && (
            <span className="tag" style={{ background: "#2A2210", color: C.warn }} title="Numbers come from simulated history until your home has 3 weeks of its own data">
              {insights?.data_source === "mixed" ? "Partly simulated data" : "Simulated data"}
            </span>
          )}
        </div>
      )}

      <section className="card">
        <h2 className="h-label">Planned for the next hour</h2>
        {plan.map((p) => {
          const sure = `${Math.round(p.p_on * 100)}%`;
          return (
            <div key={p.device} className="list-row" style={{ alignItems: "flex-start" }}>
              <span className="num" style={{ fontWeight: 700, color: "#8FB8FF", width: 48, flex: "none" }}>{p.time}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{p.title}</div>
                <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{p.why}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
                  <div className="conf" style={{ maxWidth: 160 }}><span style={{ width: sure }} /></div>
                  <span className="muted num" style={{ fontSize: 12 }}>{sure} sure</span>
                </div>
              </div>
            </div>
          );
        })}
        {!plan.length && <div className="empty">Nothing planned right now.</div>}
        <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          A plan only runs if the sensors agree at that moment: lights wait until you walk in, fans pre-cool only when
          someone is home. Devices that use more power need more certainty (around {actAt}%).
        </div>
      </section>

      <section className="card">
        <h2 className="h-label">Waiting for your OK</h2>
        {sugg.map((g) => {
          const sure = `${Math.round(g.confidence * 100)}%`;
          const accepted = g.response === "accept";
          return (
            <div key={g.id} className="list-row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{g.title}</div>
                <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
                  {g.why} · <span className="num">{sure}</span> sure · <span className="num">{clock(g.at)}</span>
                </div>
                {expired[g.id] ? (
                  <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>This suggestion expired — the room changed.</div>
                ) : !g.response ? (
                  <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                    <button type="button" className="btn btn-light" onClick={() => answer(g.id, g, true)}>Yes</button>
                    <button type="button" className="btn" onClick={() => answer(g.id, g, false)}>Not now</button>
                  </div>
                ) : (
                  <div style={{ fontSize: 13, marginTop: 8, color: accepted ? C.good : C.muted }}>
                    {accepted ? g.done_text ?? "Done." : "Okay. Your home will ask less at this time."}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {!sugg.length && <div className="empty">No questions from the AI today.</div>}
        <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>Questions disappear after 15 minutes or when the room changes.</div>
      </section>

      <section className="card">
        <h2 className="h-label">Energy and the AI today</h2>
        <div className="big-metrics" style={{ marginTop: 4 }}>
          <div><div className="bm-v num" style={{ color: C.good }}>{wh(energy?.saved_by_ai_wh)}</div><div className="bm-l">Saved by switching off</div></div>
          <div><div className="bm-v num">{wh(energy?.wasted_by_ai_wh)}</div><div className="bm-l">Used by wrong guesses</div></div>
          <div>
            <div className="bm-v num" style={{ color: (energy?.ai_net_wh ?? 0) >= 0 ? C.good : C.bad }}>{wh(energy?.ai_net_wh)}</div>
            <div className="bm-l">Net saving</div>
          </div>
        </div>
        <div className="muted" style={{ fontSize: 12.5, marginTop: 12 }}>
          {aiOns ? <>Switched something on for you <b className="num">{aiOns}</b> times · someone came <b className="num">{stats.ai_hit ?? 0}</b> times. </> : null}
          {answered ? <>You said yes to <b className="num">{stats.accepted ?? 0}</b> of <b className="num">{answered}</b> questions. </> : null}
          Saved energy is an estimate for the next hour after switching off.
        </div>
      </section>

      {(faults.length > 0 || wear.length > 0) && (
        <section className="card">
          <h2 className="h-label">Power faults</h2>
          {faults.map((a) => (
            <div key={`${a.device}-${a.at}`} className="list-row" style={{ alignItems: "flex-start" }}>
              <span className="dot" style={{ background: C.warn, marginTop: 6 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{a.title ?? `${labels[a.device] ?? a.device}: unusual power`}</div>
                <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{a.text}</div>
              </div>
              <span className="muted num" style={{ fontSize: 12 }}>{clock(a.at)}</span>
            </div>
          ))}
          {wear.map(([dev, rise]) => (
            <div key={dev} className="list-row">
              <span className="dot" style={{ background: C.muted }} />
              <span style={{ flex: 1 }}>{labels[dev] ?? dev} uses <b className="num">{Math.round(rise * 100)}%</b> more power than a month ago — check it</span>
            </div>
          ))}
          <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>Learned from each device&apos;s normal draw on its INA226 sensor.</div>
        </section>
      )}

      {(presence?.arrival_weekday || now) && (
        <section className="card">
          <h2 className="h-label">Home and away</h2>
          {now && (
            <div className="list-row">
              <span className="dot" style={{ background: now.home ? C.good : C.muted }} />
              <span style={{ flex: 1 }}>
                {now.home ? "Someone is home now" : <>Nobody home now · <b className="num">{pct(now.p_home_60)}</b> likely someone is back within the hour</>}
              </span>
            </div>
          )}
          {presence?.arrival_weekday && !(insights?.learned ?? []).some((l) => l.includes("get home")) && (
            <div className="list-row">
              <span className="dot" style={{ background: "#8FB8FF" }} />
              <span style={{ flex: 1 }}><Md text={`You usually get home around **${presence.arrival_weekday}** on weekdays`} /></span>
            </div>
          )}
        </section>
      )}

      <section className="card">
        <h2 className="h-label">What your home has learned</h2>
        {(insights?.learned ?? []).map((l) => (
          <div key={l} className="list-row"><span><Md text={l} /></span></div>
        ))}
        {!insights?.learned?.length && <div className="empty">Still learning — this fills in after a few weeks.</div>}
      </section>

      <section className="card">
        <h2 className="h-label">How the model is doing</h2>
        <div className="big-metrics" style={{ marginTop: 4 }}>
          <div><div className="bm-v num">{m ? pct(m.within_15) : "—"}</div><div className="bm-l">Right within 15 min</div></div>
          <div><div className="bm-v num">{m ? pct(m.exact) : "—"}</div><div className="bm-l">Right to the minute</div></div>
          <div><div className="bm-v num">{m ? clock(m.retrained_at) : "—"}</div><div className="bm-l">Last retrained</div></div>
        </div>
        {m?.f1 !== undefined && m?.baseline_f1 !== undefined && (
          <div style={{ fontSize: 13, marginTop: 12 }}>
            Score <b className="num">{m.f1.toFixed(2)}</b> vs <b className="num">{m.baseline_f1.toFixed(2)}</b> for simply repeating yesterday.
          </div>
        )}
        <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          {m?.model ?? "Gradient Boosting"}, trained nightly on your hub with the last 8 weeks. When you change a device by hand,
          the AI leaves it alone for {pauseH} hours and learns from it.
          {simulated ? " These numbers come from simulated history until your home has 3 weeks of its own data." : ""}
        </div>
      </section>

      {devices.length > 0 && (
        <section className="card">
          <h2 className="h-label">Each device</h2>
          {devices.map(([id, d]) => {
            const st = aiStatusText(d.status, d.days);
            const drivers = topDrivers(d.importance);
            return (
              <div key={id} className="list-row" style={{ alignItems: "flex-start" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600 }}>{labels[id] ?? id}</span>
                    <span className="tag" style={{ background: "#1F2227", color: st.color }}>{st.text}</span>
                  </div>
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 3 }}>
                    Acts at <span className="num">{pct(d.act)}</span> sure · asks from <span className="num">{pct(d.suggest)}</span>
                    {d.within_15 !== undefined && <> · right within 15 min <span className="num">{pct(d.within_15)}</span></>}
                  </div>
                  {drivers.map(([name, v]) => (
                    <div key={name} style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
                      <span style={{ fontSize: 12.5, width: 108, flex: "none", color: "#C5CAD1" }}>{name}</span>
                      <div className="conf" style={{ maxWidth: 140 }}><span style={{ width: `${v}%` }} /></div>
                      <span className="muted num" style={{ fontSize: 12 }}>{v}%</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
          <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
            Bars show what each prediction depends on most. &quot;Relearning&quot; means your routine changed, so the AI asks more and acts less for a while.
          </div>
        </section>
      )}
    </>
  );
}
