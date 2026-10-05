# Notes for Claude (and humans)

**Start every new conversation by reading `docs/PROJECT_CONTEXT.md`** — full decisions, hardware, status and next steps.

- Graduation smart-home project. Talk to the team in **Arabic**; code, UI text and commits in English.
- `docs/contract.md` is the source of truth for every Firebase path and MQTT topic.
  Changing a shape = update `docs/contract.md`, `web/lib/contract.ts`, `pi/firebase_writer.py` together.
- Web: Next.js 15 static export (`output: "export"`), Tailwind v4, Firebase JS SDK v11.
  Only `web/lib/home.ts` imports `firebase/database`. Screens use its hooks.
  Rooms/devices are drawn from `/config` — never hard-code a device in the UI.
  Dynamic pages use query params (`/room?id=living`) because of static export.
- UI = the canvas prototype 1:1: `web/app/globals.css` is the prototype CSS, `components/Icons.tsx` its SVGs.
  Use the prototype class names (`card`, `tile`, `scene`, `btn`, `list-row`, `big-metrics`, …), not new styles.
  Words/colours derived from data live in `web/lib/view.ts`.
- `/events` is written by the Pi only. The web writes only `/commands`, `/ai_pause`, `/prefs`, `/suggestions/{id}`, `/alerts/{id}/ack`, `/config`.
- `pi/` uses the Python standard library for the simulator; `sim_house.py` must behave like the real Pi services.
- AI: `ai/smart_home_ai.py` (HistGradientBoosting, 60 min horizon, acts ≥0.80, suggests ≥0.60, only switches ON).
- Real Firebase project: `ai-home-aef50` (alias `prod` in .firebaserc; default stays the `demo-smart-home` emulator). See docs/DEPLOY.md.
- Timezone: Asia/Hebron. Timestamps in Firebase: ms since epoch (UTC).
