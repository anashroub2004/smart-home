import type { CSSProperties } from "react";

/** Renders "**bold**" segments (used by AI insight sentences and alert lines). */
export function Md({ text, strong }: { text: string; strong?: CSSProperties }) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return (
    <>
      {parts.map((p, i) => (i % 2 ? <b key={i} className="num" style={strong}>{p}</b> : <span key={i}>{p}</span>))}
    </>
  );
}
