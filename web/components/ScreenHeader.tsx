"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { IBack } from "./Icons";

/** Back button + title, used by Room, Activity, History, Settings, Add device. */
export function ScreenHeader({ title, back = "/", right }: { title: string; back?: string; right?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <Link href={back} className="icon-btn" aria-label="Back">
        <IBack size={20} />
      </Link>
      <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700 }}>{title}</h1>
      {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
    </div>
  );
}
