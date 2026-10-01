"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useConfig } from "@/lib/home";
import { sortedRooms } from "@/lib/view";
import { IEnergy, IHome, IHouse, IShield, ISparkles } from "./Icons";

const TABS = [
  { href: "/", label: "Home", icon: IHome, match: ["/", "/room", "/activity", "/history", "/settings"] },
  { href: "/energy", label: "Energy", icon: IEnergy, match: ["/energy"] },
  { href: "/security", label: "Security", icon: IShield, match: ["/security"] },
  { href: "/insights", label: "Insights", icon: ISparkles, match: ["/insights"] },
];

export function Nav() {
  const path = usePathname();
  const { data: config } = useConfig();
  const rooms = sortedRooms(config).length;
  const on = (match: string[]) => match.some((m) => (m === "/" ? path === "/" : path.startsWith(m)));

  return (
    <nav className="tabs" aria-label="Main">
      <div className="brand" style={{ alignItems: "center", gap: 10, padding: "0 12px 20px" }}>
        <div style={{ width: 34, height: 34, borderRadius: 11, background: "#ECEEF1", color: "#141518", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <IHouse size={18} sw={2} />
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: 15 }}>{config?.home_name ?? "My Home"}</div>
          <div style={{ fontSize: 12, color: "#9BA1AA" }}>{rooms} rooms</div>
        </div>
      </div>
      {TABS.map(({ href, label, icon: Icon, match }) => (
        <Link key={href} href={href} className={on(match) ? "tab on" : "tab"} aria-current={on(match) ? "page" : undefined}>
          <Icon size={22} />
          {label}
        </Link>
      ))}
    </nav>
  );
}
