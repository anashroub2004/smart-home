"use client";

import { Md } from "@/components/Md";
import { answerSuggestion, useAiInsights, useAiSchedule, useConfig, useSuggestions } from "@/lib/home";
import { clock } from "@/lib/format";
import { isToday } from "@/lib/view";

export default function InsightsPage() {
  const { data: config } = useConfig();
  const { data: schedule } = useAiSchedule();
  const { list: suggestions } = useSuggestions();
  const { data: insights } = useAiInsights();

  const actAt = Math.round((config?.thresholds.ai_act_at ?? 0.8) * 100);
  const pauseH = (config?.thresholds.override_pause_min ?? 120) / 60;
  const plan = Object.values(schedule ?? {})
    .filter((d) => (d.action === "schedule_on" || d.action === "keep_on") && d.title)
    .sort((a, b) => (a.time ?? "").localeCompare(b.time ?? ""));
  const sugg = suggestions.filter((s) => isToday(s.at)).slice(0, 6);
  const m = insights?.metrics;

  return (
    <>
      <h1 style={{ margin: 0, fontSize: 28, fontWeight: 700 }}>Insights</h1>
      <p className="muted" style={{ margin: "-12px 0 0", fontSize: 14 }}>
        Your home learns your routine and prepares rooms before you need them.
      </p>

      <section className="card">
        <h2 className="h-label">Planned for the next hour</h2>
        {plan.map((p) => {
          const pct = `${Math.round(p.p_on * 100)}%`;
          return (
            <div key={p.device} className="list-row" style={{ alignItems: "flex-start" }}>
              <span className="num" style={{ fontWeight: 700, color: "#8FB8FF", width: 48, flex: "none" }}>{p.time}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{p.title}</div>
                <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{p.why}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
                  <div className="conf" style={{ maxWidth: 160 }}><span style={{ width: pct }} /></div>
                  <span className="muted num" style={{ fontSize: 12 }}>{pct} sure</span>
                </div>
              </div>
            </div>
          );
        })}
        {!plan.length && <div className="empty">Nothing planned right now.</div>}
        <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          The AI acts on its own only when it is at least {actAt}% sure. Below that, it asks you.
        </div>
      </section>

      <section className="card">
        <h2 className="h-label">Waiting for your OK</h2>
        {sugg.map((g) => {
          const pct = `${Math.round(g.confidence * 100)}%`;
          const accepted = g.response === "accept";
          return (
            <div key={g.id} className="list-row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{g.title}</div>
                <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
                  {g.why} · <span className="num">{pct}</span> sure · <span className="num">{clock(g.at)}</span>
                </div>
                {!g.response ? (
                  <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                    <button type="button" className="btn btn-light" onClick={() => answerSuggestion(g.id, g, true)}>Yes</button>
                    <button type="button" className="btn" onClick={() => answerSuggestion(g.id, g, false)}>Not now</button>
                  </div>
                ) : (
                  <div style={{ fontSize: 13, marginTop: 8, color: accepted ? "#34C759" : "#9BA1AA" }}>
                    {accepted ? g.done_text ?? "Done." : "Okay. Your home will learn from this."}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {!sugg.length && <div className="empty">No questions from the AI today.</div>}
      </section>

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
          <div><div className="bm-v num">{m ? `${Math.round(m.within_15 * 100)}%` : "—"}</div><div className="bm-l">Right within 15 min</div></div>
          <div><div className="bm-v num">{m ? `${Math.round(m.exact * 100)}%` : "—"}</div><div className="bm-l">Right to the minute</div></div>
          <div><div className="bm-v num">{m ? clock(m.retrained_at) : "—"}</div><div className="bm-l">Last retrained</div></div>
        </div>
        <div className="muted" style={{ fontSize: 12.5, marginTop: 12 }}>
          {m?.model ?? "Gradient Boosting"}, trained nightly on your hub. When you change a device by hand, the AI leaves it alone for {pauseH} hours and learns from it.
        </div>
      </section>
    </>
  );
}
