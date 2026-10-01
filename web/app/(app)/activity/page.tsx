"use client";

import Link from "next/link";
import { useEffect } from "react";
import { ScreenHeader } from "@/components/ScreenHeader";
import { useAlerts } from "@/lib/home";
import { markActivitySeen } from "@/lib/seen";
import { clock } from "@/lib/format";
import { ALERT_COLOR, isToday } from "@/lib/view";

const ACTION = { security: "View security", energy: "View energy", settings: "View system", insights: "View insights" };

export default function ActivityPage() {
  const { list, loading } = useAlerts(40);
  useEffect(() => {
    markActivitySeen();
    return markActivitySeen;
  }, []);

  return (
    <>
      <ScreenHeader title="Activity" right={<Link href="/history" className="btn" style={{ minHeight: 36 }}>Full history</Link>} />
      <section className="card">
        {list.map((a) => (
          <div key={a.id} className="list-row" style={{ alignItems: "flex-start" }}>
            <span className="dot" style={{ background: ALERT_COLOR[a.level], marginTop: 7 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 700 }}>{a.title}</div>
              <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{a.where}</div>
              <Link href={`/${a.go}`} className="btn" style={{ minHeight: 34, marginTop: 8, fontSize: 13 }}>{ACTION[a.go] ?? "Open"}</Link>
            </div>
            <span className="muted num" style={{ fontSize: 13 }}>
              {isToday(a.at) ? clock(a.at) : new Date(a.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
            </span>
          </div>
        ))}
        {!loading && !list.length && <div className="empty">Nothing needs your attention.</div>}
      </section>
    </>
  );
}
