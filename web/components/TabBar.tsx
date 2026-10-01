"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Home, Settings, Shield, Sparkles, Zap } from "lucide-react";

const TABS = [
  { href: "/", label: "Home", icon: Home },
  { href: "/energy", label: "Energy", icon: Zap },
  { href: "/security", label: "Security", icon: Shield },
  { href: "/insights", label: "Insights", icon: Sparkles },
  { href: "/history", label: "History", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function TabBar() {
  const path = usePathname();
  const isActive = (href: string) => (href === "/" ? path === "/" || path.startsWith("/room") : path.startsWith(href));

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-bg/90 backdrop-blur
                 md:inset-y-0 md:right-auto md:w-56 md:border-t-0 md:border-r md:px-3 md:py-6"
    >
      <div className="hidden px-3 pb-6 text-lg font-bold md:block">Smart Home</div>
      <ul className="flex justify-around md:flex-col md:gap-1">
        {TABS.map(({ href, label, icon: Icon }) => {
          const active = isActive(href);
          return (
            <li key={href}>
              <Link
                href={href}
                className={`flex flex-col items-center gap-1 px-2 py-2.5 text-[11px] md:flex-row md:gap-3 md:rounded-xl md:px-3 md:text-sm ${
                  active ? "text-text md:bg-surface" : "text-muted hover:text-text"
                }`}
              >
                <Icon size={20} />
                <span>{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
