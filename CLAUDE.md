# Notes for Claude (and humans)

**Start every new conversation by reading `docs/PROJECT_CONTEXT.md`** — full decisions, hardware, status and next steps.
**End of every work session:** write an update file `docs/updates/YYYY-MM-DD_NN_<topic>.md` from `docs/updates/TEMPLATE.md` (in Arabic) and include it in the commit/patch — other chats can't see this one.
**Team split:** `docs/TEAM.md` — each chat works on ONE part (web/ pi/ ai/ firmware/) and never edits another part or the shared contract files without agreement.

- Graduation smart-home project. Talk to the team in **Arabic**; code, UI text and commits in English.
- `docs/contract.md` is the source of truth for every Firebase path and MQTT topic.
  Changing a shape = update `docs/contract.md`, `web/lib/contract.ts`, `pi/firebase_writer.py` together.
- Web: Next.js 15 static export (`output: "export"`), Tailwind v4, Firebase JS SDK v11.
  Only `web/lib/home.ts` imports `firebase/database`. Screens use its hooks.
  Rooms/devices are drawn from `/config` — never hard-code a device or a device type anywhere.
  Device = capabilities (`caps`), permissions (`control`), `rules`, `hw` — see docs/contract.md + web/lib/templates.ts.
  Dynamic pages use query params (`/room?id=living`) because of static export.
- UI = the canvas prototype 1:1: `web/app/globals.css` is the prototype CSS, `components/Icons.tsx` its SVGs.
  Use the prototype class names (`card`, `tile`, `scene`, `btn`, `list-row`, `big-metrics`, …), not new styles.
  Words/colours derived from data live in `web/lib/view.ts`.
- `/events` is written by the Pi only. The web writes only `/commands`, `/ai_pause`, `/prefs`, `/suggestions/{id}`, `/alerts/{id}/ack`, `/config`.
- `pi/` uses the Python standard library for the simulator; `sim_house.py` must behave like the real Pi services.
- AI: package `ai/` (see ai/README_AR.md + the smart-home-ai-model skill). HistGradientBoosting per device, 60 min horizon,
  predict -> sensor gate -> waste guard -> smart off; energy-scaled + adaptive thresholds; 8-week window.
  `ai.runtime.AIRuntime` is used by the simulator and (later) the Pi automation service. Tests: `python -m unittest discover -s ai/tests -t .`
- Digital twin: `python pi/sim_house.py --twin-only --speed 60` -> http://localhost:8765 (pi/twin/). The person follows
  `ai/routine.py` (the same routine the AI trains on). Scenarios in pi/scenarios.py must all PASS before AI/rule changes ship.
- Real Firebase project: `ai-home-aef50` (alias `prod` in .firebaserc; default stays the `demo-smart-home` emulator). See docs/DEPLOY.md.
- Timezone: Asia/Hebron. Timestamps in Firebase: ms since epoch (UTC).
