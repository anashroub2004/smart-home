"""Learning curve — does the house need you less and less? (headless, no Firebase, no twin page)

Runs the simulated house for N days as fast as the computer allows (the virtual clock jumps forward in fixed
steps), starting from a brand-new hub: 10 weeks of simulated history, first training, then the person lives his
routine and answers the AI's questions the way he would want it. Retraining runs every night at 03:00 as usual.

    python pi/learning_curve.py --days 28            the AI with the trust ladder
    python pi/learning_curve.py --days 28 --no-ai    rules only (the baseline to compare with)

Writes pi/twin/reports/learning_curve_<when>_<ai|rules>.md / .json / .svg — one row per day:
    manual fixes      the person had to do it himself (light on in the dark, light off when lying down)
    asked / yes / no  the AI's questions and his answers
    automatic         what the AI did by itself (switch-ons, smart off, learned actions)
    learned           actions it does without asking because he said yes often enough (trust ladder)
    asleep, light on  minutes a light burned while he slept in that room
"""
import argparse
import json
import shutil
import sys
import time
from argparse import Namespace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_house  # noqa: E402
from memory_db import MemoryDB  # noqa: E402
from sim_house import CLOCK, ROOT, House, Writer, load_ai, seed  # noqa: E402

REPORTS = Path(__file__).resolve().parent / "twin" / "reports"
COLS = [("manual_fix", "Manual fixes"), ("asked", "Asked"), ("yes", "Yes"), ("no", "No"), ("unanswered", "No answer"),
        ("ai_auto", "Automatic"), ("ai_learned", "Learned (no question)"), ("undone", "Undone"),
        ("asleep_light_min", "Asleep, light on (min)"), ("manual", "All manual presses")]


def args_for(no_ai):
    return Namespace(fast=False, offline=None, fail_rate=0.0, lockout=False, reset=False, cloud=None, owner_uid=None,
                     fault=None, no_ai=no_ai, speed=0, twin_only=True, twin_port=0, no_twin=True, auto_answer=True)


def svg_chart(rows, title):
    """Small line chart: manual fixes, questions, automatic-without-asking per day."""
    W, H, L, B = 760, 300, 46, 40
    series = [("manual_fix", "#E5484D", "Manual fixes"), ("asked", "#F5A524", "Questions"),
              ("ai_learned", "#2BB3A3", "Done without asking")]
    top = max([1] + [r.get(k, 0) for r in rows for k, _, _ in series])
    n = max(1, len(rows) - 1)
    X = lambda i: L + (W - L - 20) * i / n
    Y = lambda v: H - B - (H - B - 30) * v / top
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Arial" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="#fff"/><text x="{L}" y="18" font-size="14" font-weight="bold">{title}</text>']
    for v in range(0, int(top) + 1, max(1, int(top) // 5)):
        out.append(f'<line x1="{L}" x2="{W - 20}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#eee"/>'
                   f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#666">{v}</text>')
    for i, r in enumerate(rows):
        if i % 7 == 0 or i == len(rows) - 1:
            out.append(f'<text x="{X(i):.1f}" y="{H - B + 18}" text-anchor="middle" fill="#666">day {i + 1}</text>')
    for j, (k, col, name) in enumerate(series):
        pts = " ".join(f"{X(i):.1f},{Y(r.get(k, 0)):.1f}" for i, r in enumerate(rows))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.5"/>')
        out.append(f'<rect x="{L + j * 190}" y="{H - 14}" width="12" height="4" fill="{col}"/>'
                   f'<text x="{L + 18 + j * 190}" y="{H - 9}">{name}</text>')
    out.append("</svg>")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--no-ai", action="store_true", help="rules only (baseline)")
    ap.add_argument("--step", type=float, default=10.0, help="virtual seconds per step (default 10)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verbose", action="store_true", help="print every AI action, question, answer and manual press")
    a = ap.parse_args()
    import random
    random.seed(a.seed)

    name = "curve_rules" if a.no_ai else "curve_ai"
    shutil.rmtree(ROOT / "ai" / "models" / name, ignore_errors=True)       # always a brand-new hub
    for ext in ("", "-wal", "-shm"):
        (ROOT / "ai" / "data" / f"{name}.db{ext}").unlink(missing_ok=True)

    w = Writer(MemoryDB())
    config = seed(w, False, cloud=False)
    args = args_for(a.no_ai)
    house = House(w, config, args)
    house.sync_retrain = True
    house.update_sensors()
    t0 = time.time()
    if not a.no_ai:
        parts = load_ai(args)
        if not parts:
            sys.exit("the AI is not installed: pip install -r ai/requirements.txt")
        house.start_ai(parts, name=name)
        print(f"AI ready in {time.time() - t0:.0f} s (brand-new hub: simulated history + first training)")
    CLOCK.pause()
    house.run_ai()
    house.loop_state = dict(last_tick=CLOCK.now(), real={})
    end = CLOCK.now() + a.days * 86400
    day = None
    seen = set()
    while CLOCK.now() < end:
        CLOCK.forward(a.step)
        house.step(headless=True)
        if a.verbose and house.ai:
            for sid, sg in house.ai.suggestions.items():
                if sid not in seen:
                    seen.add(sid)
                    print(f"   {sim_house.vnow():%a %H:%M}  ASK    {sg['title']}  ({sg.get('kind') or '-'}, "
                          f"{round(sg['conf'] * 100)}%)  [{house.person.activity} in {house.person.room}]")
        if a.verbose:
            for e in reversed(list(house.recent)[:10]):
                k = (e.get("at"), e.get("title"))
                if k in seen or e.get("group") not in ("ai", "manual"):
                    continue
                seen.add(k)
                print(f"   {datetime.fromtimestamp(e['at'] / 1000):%a %H:%M}  {e['group']:6} {e.get('title')}"
                      f"  [{house.person.activity} in {house.person.room}]  {e.get('short') or ''}")
        d = sim_house.vnow().strftime("%Y-%m-%d")
        if d != day:
            if day:
                r = next((x for x in house.kpi_view() if x["day"] == day), None)
                if r:
                    print(f"{day}  fixes {r['manual_fix']:3}  asked {r['asked']:3}  yes {r['yes']:3}  "
                          f"auto {r['ai_auto']:3}  learned {r['ai_learned']:3}  asleep+light {r['asleep_light_min']:4} min"
                          f"   ({time.time() - t0:.0f} s)", flush=True)
            day = d
    rows = house.kpi_view()[1:-1]                    # whole days only
    trust = house.ai.state.get("trust") if house.ai else {}
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = REPORTS / f"learning_curve_{datetime.now():%Y%m%d_%H%M}_{'rules' if a.no_ai else 'ai'}"
    stem.with_suffix(".json").write_text(json.dumps({"days": rows, "trust": trust, "args": vars(a)}, indent=2))
    write_report(stem, rows, trust, a.no_ai, a.days, a.seed)
    print(f"\nreport: {stem}.md  ({time.time() - t0:.0f} s)")


def write_report(stem, rows, trust, no_ai, days, seed):
    title = f"{'Rules only' if no_ai else 'AI + trust ladder'} · {len(rows)} days"
    stem.with_suffix(".svg").write_text(svg_chart(rows, title))
    week = lambda k, i: sum(r.get(k, 0) for r in rows[i * 7:(i + 1) * 7])
    weeks = max(1, (len(rows) + 6) // 7)
    md = [f"# Learning curve — {title}", "", f"Brand-new hub, {days} simulated days, seed {seed}.", "",
          "| Week | " + " | ".join(n for _, n in COLS) + " |", "|---" * (len(COLS) + 1) + "|"]
    for i in range(weeks):
        n = len(rows[i * 7:(i + 1) * 7])
        md.append(f"| {i + 1}{'' if n == 7 else f' ({n} days)'} | " + " | ".join(str(round(week(k, i))) for k, _ in COLS) + " |")
    md += ["", "| Day | " + " | ".join(n for _, n in COLS) + " |", "|---" * (len(COLS) + 1) + "|"]
    md += [f"| {r['day']} | " + " | ".join(str(r.get(k, 0)) for k, _ in COLS) + " |" for r in rows]
    if trust:
        md += ["", "## Trust ladder at the end", ""]
        md += [f"- {dev} · {k}: level {e['level']}" for dev, kinds in trust.items() for k, e in kinds.items()]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    main()
