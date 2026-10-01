"use client";

import { useEffect, useState } from "react";

// "Last time I opened Activity" — per browser, only for the unread badge.
const KEY = "activity_seen_at";

function read(): number {
  try {
    return Number(localStorage.getItem(KEY) || 0);
  } catch {
    return 0;
  }
}

export function markActivitySeen() {
  try {
    localStorage.setItem(KEY, String(Date.now()));
    window.dispatchEvent(new Event(KEY));
  } catch {
    /* private mode: badge just stays */
  }
}

export function useActivitySeen(): number {
  const [seen, setSeen] = useState(0);
  useEffect(() => {
    setSeen(read());
    const on = () => setSeen(read());
    window.addEventListener(KEY, on);
    return () => window.removeEventListener(KEY, on);
  }, []);
  return seen;
}
