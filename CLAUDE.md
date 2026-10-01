# Notes for Claude (and humans)

- Graduation smart-home project. Talk to the team in **Arabic**; code, UI text and commits in English.
- `docs/contract.md` is the source of truth for every Firebase path and MQTT topic.
  Changing a shape = update `docs/contract.md`, `web/lib/contract.ts`, `pi/firebase_writer.py` together.
- Web: Next.js 15 static export (`output: "export"`), Tailwind v4, Firebase JS SDK v11.
  Only `web/lib/home.ts` imports `firebase/database`. Screens use its hooks.
  Rooms/devices are drawn from `/config` — never hard-code a device in the UI.
  Dynamic pages use query params (`/room?id=living`) because of static export.
- Design tokens live in `web/app/globals.css` (dark only, Manrope).
- `/events` is written by the Pi only. The web writes only `/commands`, `/suggestions/{id}`, `/alerts/{id}/ack`, `/config`.
- `pi/` uses the Python standard library for the simulator; `sim_house.py` must behave like the real Pi services.
- AI: `ai/smart_home_ai.py` (HistGradientBoosting, 60 min horizon, acts ≥0.80, suggests ≥0.60, only switches ON).
- Timezone: Asia/Hebron. Timestamps in Firebase: ms since epoch (UTC).
