# عقد البيانات (Data Contract)

هذا الملف هو **المرجع الوحيد** لشكل البيانات بين: الـ Pi، المحاكي، تطبيق الويب، والذكاء الاصطناعي.
أي تغيير هنا يجب أن ينعكس في: `web/lib/contract.ts` و `pi/firebase_writer.py` (و `pi/sim_house.py`).

القاعدة: **الويب يكتب فقط في** `/commands` و `/ai_pause` و `/prefs` و `/suggestions/{id}/response` و `/alerts/{id}/ack` و `/config`.
كل شيء آخر يكتبه الـ Pi (أو المحاكي). كل الأوقات **ms منذ 1970 (UTC)**.

## مسارات Firebase Realtime Database

| المسار | من يكتب | الوصف |
|---|---|---|
| `/config` | الويب (owner) | الغرف والأجهزة والعتبات والمشاهد — مصدر رسم الواجهة. كل تعديل يزيد `version` |
| `/home_state` | Pi | الحالة الحية: الغرف، الأجهزة، الطاقة، الباب، المشهد الحالي |
| `/nodes/{node}` | Pi | `online`, `last_seen`, `config_version` (آخر نسخة config أكّدتها العقدة), `rssi` |
| `/commands/{device}` | الويب ← Pi | أمر واحد معلّق لكل جهاز |
| `/events/{pushId}` | Pi فقط | سجل كامل لكل عملية في البيت (شاشة History) |
| `/alerts/{pushId}` | Pi | ما يظهر في شاشة Activity، و`critical` يفتح شاشة التنبيه الحمراء |
| `/summaries/{room}/{YYYY-MM-DD}/{HH:MM}` | Pi | ملخص كل دقيقة |
| `/energy_daily/{YYYY-MM-DD}/{device}` | Pi | Wh لكل جهاز في اليوم، و`_base` = الـ Pi + العقد + الحساسات |
| `/ai_schedule/{device}` | Pi (AI) | خطة الساعة القادمة |
| `/ai_insights` | Pi (AI) | دقة النموذج + ما تعلّمه (بعد التدريب الليلي) |
| `/ai_pause/{device}` | الويب | رقم = إيقاف الأتمتة (AI + القواعد) لهذا الجهاز حتى هذا الوقت. حذفه = استئناف |
| `/suggestions/{id}` | Pi / الويب يرد | اقتراحات بثقة متوسطة |
| `/access_log/{pushId}` | Pi | محاولات فتح الباب |
| `/prefs` | الويب | تفضيلات الإشعارات |
| `/users/{uid}` | يدوي | `{ role: "owner", name }` |

## `/config` (مختصر — المثال الكامل في `docs/seed.json`)

```json
{
  "schema": 2, "version": 1, "home_name": "My Studio",
  "nodes": { "living": { "name": "Living room & kitchen node", "reserved_pins": [16,17,21,22,34,35], "i2c": ["0x44","0x23"] } },
  "rooms": {
    "living": {
      "name": "Living room", "node": "living", "order": 1,
      "sensors": ["temp","hum","lux","occ"],
      "hardware": ["C1001 mmWave","PIR","SHT31","BH1750","INA226"],
      "devices": { "living_fan": { "type": "fan", "name": "Fan", "pin": 25, "button": 32, "ina": "0x40", "watts": 2.4 } }
    },
    "entrance": { "name": "Entrance", "node": "door", "hidden": true, "devices": { "door_lock": { "type": "lock", "name": "Front door", "pin": 18, "watts": 0 } } }
  },
  "thresholds": { "fan_on_temp": 28, "fan_off_temp": 26.5, "light_on_lux": 150, "empty_room_off_min": 10,
                  "ai_act_at": 0.8, "ai_suggest_at": 0.6, "override_pause_min": 120,
                  "door_lockout_attempts": 5, "door_lockout_s": 60 },
  "scenes": { "away": { "name": "Away", "order": 2, "set": { "living_fan": 0 } },
              "sleep": { "name": "Sleep", "order": 3, "set": { "bedroom_fan": { "v": 1, "speed": 40 } } } },
  "fingerprints": { "1": "Owner", "2": "Fares" }
}
```

- `schema`: إذا تغيّر شكل البيانات نرفعه، والمحاكي يعيد التهيئة تلقائياً.
- جهاز جديد من شاشة Add device يحمل `added_at`، فتعرض الواجهة "AI is learning" لثلاثة أسابيع.
- سرعات المروحة: Low = 40، Medium = 70، High = 100.

## `/home_state`

```json
{
  "updated_at": 1759300000000, "power_w": 10.6, "base_w": 6.8, "scene": "home",
  "door": { "lockout_until": 0, "last_open": { "at": 1759299000000, "method": "fingerprint", "who": "Fares" } },
  "rooms": { "living": { "temp": 27.4, "hum": 48, "lux": 120, "occ": 1, "occ_since": 1759299000000, "occ_by": "mmwave" } },
  "devices": { "living_fan": { "v": 1, "speed": 70, "src": "ai", "at": 1759299000000, "watts": 1.7 } }
}
```

## `/commands/{device}`

```json
{ "v": 1, "speed": 70, "by": "<uid>", "at": 1759300000000, "status": "pending" }
```

اختياري: `scene` (أمر من مشهد)، `via: "suggestion"` + `confidence` (المستخدم وافق على اقتراح AI).
الـ Pi ينفذ ثم يغيّر `status` إلى `done` أو `failed` (+ `error`). بعد 10 ثوانٍ بدون رد يعرض الويب "didn't respond".

## `/events/{pushId}` — السجل الكامل

```json
{
  "at": 1759300000123, "kind": "device", "group": "ai",
  "title": "Living room fan turned on", "short": "Pre-cooling before you got home",
  "source": "ai", "src_label": "AI · 86%", "by_label": "AI (Gradient Boosting)",
  "why": "Predicted you would be home and the room above 28°C · 86% sure",
  "change": "Off → On · Medium", "device": "living_fan", "room": "living", "node": "living",
  "confidence": 0.86, "from": { "v": 0 }, "to": { "v": 1, "speed": 70 },
  "result": "ok", "latency_ms": 700, "tags": ["ai"]
}
```

| الحقل | القيم |
|---|---|
| `kind` | `device` · `door` · `motion` · `node` · `ai` · `rule` · `scene` · `alert` · `config` |
| `group` | `manual` · `ai` · `rule` · `door` · `system` — فلتر History واللون |
| `source` | `web` · `button` · `ai` · `rule` · `keypad` · `fingerprint` · `exit_button` · `system` |
| `result` | `ok` · `failed` · `denied` |
| `saved_wh` | للقاعدة "الغرفة فارغة" — يظهر في شاشة Energy (Waste stopped) |

## `/alerts/{pushId}`

```json
{ "at": 1759300000000, "level": "critical", "title": "Someone is trying to get in",
  "where": "Front door · keypad · 5 wrong PINs", "go": "security",
  "lines": ["**5 wrong PIN attempts** at the front door", "Keypad is blocked for 60 s."], "ack": false }
```

`level`: `critical` (شاشة حمراء كاملة) · `warning` · `info` · `good`. `go`: الشاشة التي يفتحها الزر.

## `/ai_schedule/{device}` و `/suggestions/{id}` و `/ai_insights`

```json
{ "device": "bedroom_fan", "p_on": 0.84, "action": "schedule_on", "time": "19:30",
  "title": "Bedroom fan turns on", "why": "Pre-cooling: the bedroom is 26.1°C ...", "predicted_for": "2026-10-01T19:30" }
```

`action`: `schedule_on` · `keep_on` · `suggest_on` · `suggest_off` · `paused_by_override` · `none`.

```json
{ "device": "living_fan", "action": "off", "confidence": 0.68, "title": "Turn off the living room fan?",
  "why": "The room is cooling down ...", "at": 1759300000000, "response": "accept", "handled": true, "done_text": "Done." }
```

```json
{ "metrics": { "within_15": 0.93, "exact": 0.75, "retrained_at": 1759287600000, "model": "Gradient Boosting" },
  "learned": ["You usually get home around **16:30** on weekdays."] }
```

## `/summaries/{room}/{day}/{HH:MM}`

```json
{ "temp": 27.4, "hum": 48, "lux": 120, "occ": 1, "watts": 2.9, "dev": { "living_fan": 1, "living_light": 1 } }
```

`dev` يُستخدم لرسم "Last 24 hours" و"On today" في نافذة الجهاز.

## MQTT (داخل البيت فقط)

| Topic | الاتجاه | مثال |
|---|---|---|
| `home/<room>/<sensor>` | ESP32 → Pi | `home/living/temp` → `29.1` |
| `home/<room>/<device>/state` | ESP32 → Pi | `{"v":1,"speed":70,"src":"button"}` |
| `home/<room>/<device>/set` | Pi → ESP32 | `{"v":1,"speed":70}` |
| `home/door/event` | ESP32 → Pi | `{"type":"fingerprint","ok":true,"id":2}` |
| `home/door/open` | Pi → ESP32 | `{}` |
| `home/<node>/status` | LWT | `online` / `offline` (retained) |
| `home/<node>/config` | Pi → ESP32 | الجزء الخاص بالعقدة من `/config` (retained) |
| `home/<node>/config/ack` | ESP32 → Pi | `{"version":3}` → يُكتب في `/nodes/<node>/config_version` |
