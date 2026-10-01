"use client";

import { Bot, Check, X } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { answerSuggestion, useAiSchedule, useSuggestions } from "@/lib/home";
import { timeAgo } from "@/lib/format";

export default function InsightsPage() {
  const { list: suggestions } = useSuggestions();
  const { data: schedule } = useAiSchedule();
  const decisions = Object.values(schedule ?? {}).sort((a, b) => b.p_on - a.p_on);

  return (
    <>
      <PageHeader title="Insights" sub="The AI predicts each device 60 minutes ahead. It only switches devices on; rules switch them off." />

      <h2 className="mb-2 mt-6 text-lg font-bold">Suggestions</h2>
      {suggestions.length ? (
        <ul className="space-y-3">
          {suggestions.map((s) => (
            <li key={s.id} className="card flex items-center gap-3 p-4">
              <Bot size={20} className="text-accent" />
              <div className="flex-1">
                <p className="text-sm">{s.text}</p>
                <p className="text-xs text-muted">{Math.round(s.confidence * 100)}% confident · {timeAgo(s.at)}</p>
              </div>
              <button aria-label="Dismiss" onClick={() => answerSuggestion(s.id, s, false)} className="grid h-9 w-9 place-items-center rounded-full bg-tile text-muted"><X size={16} /></button>
              <button aria-label="Accept" onClick={() => answerSuggestion(s.id, s, true)} className="grid h-9 w-9 place-items-center rounded-full bg-text text-bg"><Check size={16} /></button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">No suggestions right now.</p>
      )}

      <h2 className="mb-2 mt-8 text-lg font-bold">Next hour</h2>
      {decisions.length ? (
        <ul className="card divide-y divide-border px-4">
          {decisions.map((d) => (
            <li key={d.device} className="flex items-center gap-3 py-3 text-sm">
              <span className="flex-1">{d.device}</span>
              <span className="text-muted">{d.action.replaceAll("_", " ")}</span>
              <span className="w-24">
                <span className="block h-1.5 rounded-full bg-tile">
                  <span className="block h-full rounded-full bg-accent" style={{ width: `${d.p_on * 100}%` }} />
                </span>
              </span>
              <span className="w-10 text-right text-muted">{Math.round(d.p_on * 100)}%</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">No predictions yet — the AI service writes here every 15 minutes.</p>
      )}
    </>
  );
}
