"use client";

import { useRouter } from "next/navigation";
import { ackAlert, useAlerts } from "@/lib/home";
import { IWarn } from "./Icons";
import { Md } from "./Md";

const SHOW_FOR_MS = 15 * 60_000;

/** Full-screen red alert (prototype "Critical alert") for an unacknowledged critical alert. */
export function AlertOverlay() {
  const { list } = useAlerts(20);
  const router = useRouter();
  const a = list.find((x) => x.level === "critical" && !x.ack && Date.now() - x.at < SHOW_FOR_MS);
  if (!a) return null;

  return (
    <div className="alert-screen" role="alertdialog" aria-label="Security alert">
      <div style={{ width: 72, height: 72, borderRadius: "50%", background: "#FF5A52", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", marginTop: "6vh" }}>
        <IWarn size={34} />
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#FF8A84", letterSpacing: ".06em", textTransform: "uppercase" }}>Security alert</div>
        <h1 style={{ margin: "6px 0 0", fontSize: 30, fontWeight: 700, lineHeight: 1.15 }}>{a.title}</h1>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 15, color: "#F2C9C6" }}>
        {(a.lines ?? [a.where]).map((l, i) => (
          <div key={i} className="num"><Md text={l} strong={{ color: "#fff" }} /></div>
        ))}
      </div>
      <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 10, maxWidth: 480, width: "100%" }}>
        <button type="button" className="btn btn-danger btn-block" onClick={() => { ackAlert(a.id); router.push("/security"); }}>
          View camera clip
        </button>
        <button type="button" className="btn btn-block" style={{ background: "#2A1716" }} onClick={() => ackAlert(a.id)}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
