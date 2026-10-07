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
| `/energy_waste/{YYYY-MM-DD}/{device}/{cause}` | Pi (AI) | Wh استُهلكت والغرفة **فارغة مؤكدة**. `cause`: `forgotten` · `rule_delay` · `ai` · `standby` |
| `/ai_schedule/{device}` | Pi (AI) | خطة الساعة القادمة |
| `/ai_insights` | Pi (AI) | دقة النموذج، ما تعلّمه، أهمية العوامل، الطاقة، الأعطال (بعد التدريب الليلي + تحديثات حية) |
| `/ai_pause/{device}` | الويب | رقم = إيقاف الأتمتة (AI + القواعد) لهذا الجهاز حتى هذا الوقت. حذفه = استئناف |
| `/suggestions/{id}` | Pi / الويب يرد | اقتراحات بثقة متوسطة. **الـ Pi يحذفها** بعد 15 دقيقة أو عند تغيّر الوضع (السجل يبقى في `/events`) |
| `/access_log/{pushId}` | Pi | محاولات فتح الباب |
| `/prefs` | الويب | تفضيلات الإشعارات |
| `/users/{uid}` | يدوي | `{ role: "owner", name }` |

## `/config` (المثال الكامل في `docs/seed.json`)

```json
{
  "schema": 3, "version": 1, "home_name": "My Studio",
  "nodes": { "living": { "name": "Living room & kitchen node", "reserved_pins": [16,17,21,22,34,35], "i2c": ["0x44","0x23"] } },
  "rooms": {
    "living": {
      "name": "Living room", "node": "living", "order": 1,
      "sensors": ["temp","hum","lux","occ"],
      "hardware": ["C1001 mmWave","PIR","SHT31","BH1750","INA226"],
      "devices": { "living_fan": { ...انظر "نموذج الجهاز" أدناه... } }
    },
    "entrance": { "name": "Entrance", "node": "door", "hidden": true, "devices": { "door_lock": { ... } } }
  },
  "thresholds": { "fan_on_temp": 28, "fan_off_temp": 26.5, "light_on_lux": 150, "empty_room_off_min": 10,
                  "ai_act_at": 0.8, "ai_suggest_at": 0.6, "override_pause_min": 120,
                  "door_lockout_attempts": 5, "door_lockout_s": 60 },
  "scenes": { "sleep": { "name": "Sleep", "order": 3, "set": { "bedroom_fan": { "v": 1, "level": 40 } } } }
}
```

## نموذج الجهاز (Capabilities) — أي جهاز، بدون كود

كل جهاز يصف **ماذا يستطيع** و**من يُسمح له**. الواجهة والمحاكي والـ Pi والذكاء الاصطناعي يقرؤون هذا الوصف — لا توجد أنواع ثابتة في الكود.

```json
"living_fan": {
  "name": "Fan", "icon": "fan", "template": "fan",
  "caps":    { "power": "write", "level": { "steps": [40,70,100], "labels": ["Low","Medium","High"] }, "energy": "ina" },
  "control": { "app": true, "button": true, "rules": true, "ai": true },
  "rules":   { "off_when_empty": true, "follow_temp": true },
  "hw":      { "out": "pwm", "pin": 25, "button": 32, "ina": "0x40" },
  "watts": 2.4,
  "added_at": 1759300000000
}
```

| الحقل | القيم | المعنى |
|---|---|---|
| `caps.power` | `write` · `read` | **الحد الأدنى لكل جهاز.** `write` = نتحكم به، `read` = مراقبة فقط (نعرف حالته من التيار) |
| `caps.energy` | `ina` · `estimate` · `none` | قراءة الاستهلاك: INA226، أو تقدير من `watts`، أو لا شيء |
| `caps.level` | `{steps, labels}` | سرعة / تعتيم (اختياري) |
| `caps.mode` | `{options}` | أوضاع مثل Cool/Heat (اختياري) |
| `caps.status` | `true` | حالة نصية مثل Running (اختياري) |
| `caps.lock` | `{open_s}` | قفل يفتح مؤقتاً (الباب) |
| `control.app/button/rules/ai` | `true/false` | من يُسمح له بالتغيير. جهاز مراقبة = كلها `false` |
| `control.confirm` | `true` | يطلب تأكيداً قبل التنفيذ |
| `rules.off_when_empty` | | يُطفأ بعد فراغ الغرفة `empty_room_off_min` دقيقة |
| `rules.on_when_dark` | | يعمل عند الظلام **لحظة دخول شخص** للغرفة (خلال 90 ث من بدء الوجود، `light_on_lux`) — لا يعيد تشغيل ضوء أطفأه شخص نائم أو جالس |
| `rules.follow_temp` | | يعمل فوق `fan_on_temp` ويُطفأ تحت `fan_off_temp` |
| `rules.alert_if_off_min` | رقم | للمراقبة: تنبيه إذا توقف أكثر من N دقيقة (ثلاجة) |
| `hw.out` | `relay` · `pwm` · `ir` · `servo` · `none` | كيف تشغّله العقدة |
| `hw.pin` / `hw.button` / `hw.ina` | | طرف الخرج / زر الحائط / عنوان INA226 |
| `hw.on_above_w` | رقم | للمراقبة: يعتبر "يعمل" فوق هذه القدرة |
| `icon` | `fan` `light` `washer` `lock` `tv` `fridge` `ac` `heater` `pump` `plug` `generic` | الشكل فقط — أي قيمة جديدة تظهر بأيقونة عامة |
| `ai` (اختياري) | `{act, lead_min, off_after_min, grace_min, temp_on}` | تعديل سلوك الـ AI لهذا الجهاز. كل قيمة لها افتراضي (انظر أدناه) |

**الذكاء الاصطناعي:** كل جهاز `power: write` + `control.ai: true` يحصل على نموذج خاص تلقائياً.
الأجهزة `power: read` في نفس الغرفة تدخل كمعلومات إضافية (مثلاً: التلفاز يعمل ← هناك شخص).
نوع الجهاز للـ AI يُستنتج: `light` (icon light أو `on_when_dark`) · `thermal` (fan/ac/heater أو `follow_temp`) · `generic`.

| إعداد `ai` | الافتراضي |
|---|---|
| `act` | عتبة التنفيذ محسوبة من الاستهلاك: `ai_act_at − 0.15 + 0.085·log10(1+watts)` ← ضوء 1.2W ≈ 0.68، مروحة 2.4W ≈ 0.70، مكيف 1.5kW ≈ 0.92 |
| `lead_min` | 15 للأجهزة الحرارية (تبريد مسبق)، 0 لغيرها. **الأضواء لا تُشغَّل مسبقاً أبداً** |
| `off_after_min` | إطفاء ذكي بعد فراغ مؤكد (رادار **و** PIR): أضواء 5، غيرها 10 |
| `grace_min` | حارس الهدر: إن لم يأتِ أحد حتى الموعد + 10 دقائق يطفئ الـ AI ما شغّله |
| `temp_on` | بوابة الحرارة للأجهزة الحرارية = `thresholds.fan_on_temp` |

## `/home_state`

```json
{
  "updated_at": 1759300000000, "power_w": 10.6, "base_w": 6.8, "scene": "home",
  "door": { "lockout_until": 0, "last_open": { "at": 1759299000000, "method": "fingerprint", "who": "Fares" } },
  "rooms": { "living": { "temp": 27.4, "hum": 48, "lux": 120, "occ": 1, "occ_since": 1759299000000, "occ_by": "mmwave",
                         "mmwave": 1, "pir": 0 } },
  "devices": { "living_fan": { "v": 1, "level": 70, "src": "ai", "at": 1759299000000, "watts": 1.7 },
    "kitchen_ac": { "v": 1, "mode": "Cool", "src": "web", "watts": 880 },
    "washer":     { "v": 1, "status": "Running", "src": "button", "watts": 3.5 } }
}
```

## `/commands/{device}`

```json
{ "v": 1, "level": 70, "by": "<uid>", "at": 1759300000000, "status": "pending" }
```

اختياري: `level` و `mode` (حسب قدرات الجهاز)، `scene` (أمر من مشهد)، `via: "suggestion"` + `confidence` (المستخدم وافق على اقتراح AI).
الـ Pi يرفض الأمر إذا كان الجهاز `power: read` أو `control.app: false`. وإلا ينفذ ثم يغيّر `status` إلى `done` أو `failed` (+ `error`). بعد 10 ثوانٍ بدون رد يعرض الويب "didn't respond".

## `/events/{pushId}` — السجل الكامل

```json
{
  "at": 1759300000123, "kind": "device", "group": "ai",
  "title": "Living room fan turned on", "short": "Pre-cooling before you got home",
  "source": "ai", "src_label": "AI · 86%", "by_label": "AI (Gradient Boosting)",
  "why": "Predicted you would be home and the room above 28°C · 86% sure",
  "change": "Off → On · Medium", "device": "living_fan", "room": "living", "node": "living",
  "confidence": 0.86, "from": { "v": 0 }, "to": { "v": 1, "level": 70 },
  "result": "ok", "latency_ms": 700, "tags": ["ai"]
}
```

| الحقل | القيم |
|---|---|
| `kind` | `device` · `door` · `motion` · `node` · `ai` · `rule` · `scene` · `alert` · `config` |
| `group` | `manual` · `ai` · `rule` · `door` · `system` — فلتر History واللون |
| `source` | `web` · `button` · `ai` · `rule` · `keypad` · `fingerprint` · `exit_button` · `system` |
| `result` | `ok` · `failed` · `denied` |
| `saved_wh` | عند الإطفاء بسبب غرفة فارغة (قاعدة أو AI) — **تقدير** لاستهلاك الساعة القادمة. يظهر في شاشة Energy (Waste stopped) |

أحداث الـ AI (`group: "ai"`, `source: "ai"`): تشغيل بعد فحص الحساسات (`why` ينتهي بـ `checked: ...`)،
"planned, not switched on" (البوابة رفضت)، "turned off (prediction missed)" (حارس الهدر)، "turned off — room empty" (إطفاء ذكي)،
"Suggestion expired: ...". وأعطال الطاقة: `kind: "alert"`, `group: "system"` + تنبيه `warning` إلى شاشة Energy.

## `/alerts/{pushId}`

```json
{ "at": 1759300000000, "level": "critical", "title": "Someone is trying to get in",
  "where": "Front door · keypad · 5 wrong PINs", "go": "security",
  "lines": ["**5 wrong PIN attempts** at the front door", "Keypad is blocked for 60 s."], "ack": false }
```

`level`: `critical` (شاشة حمراء كاملة) · `warning` · `info` · `good`. `go`: الشاشة التي يفتحها الزر.

## `/ai_schedule/{device}` و `/suggestions/{id}` و `/ai_insights`

```json
{ "device": "bedroom_fan", "p_on": 0.84, "action": "schedule_on", "status": "ready", "time": "19:30",
  "title": "Bedroom fan turns on", "why": "Pre-cooling · room 29.4°C · your usual time · someone is home",
  "predicted_for": "2026-10-01T19:45", "execute_at": "2026-10-01T19:30", "act_at": 0.7, "suggest_at": 0.5 }
```

`action`: `schedule_on` · `keep_on` · `suggest_on` · `suggest_off` · `paused_by_override` · `paused_by_user` · `learning` · `none`.
`schedule_on` هو **نية** فقط: التنفيذ يحدث عند `execute_at` **إذا وافقت الحساسات** (البوابة). الأضواء: تعمل عند دخولك فقط.

```json
{ "device": "living_fan", "action": "off", "confidence": 0.68, "title": "Turn off the living room fan?",
  "why": "Nobody is in the room · usually off at this time", "at": 1759300000000,
  "expires_at": 1759300900000, "response": "accept", "handled": true, "done_text": "Done." }
```

```json
{ "status": "ready", "data_source": "simulated",
  "metrics": { "within_15": 0.89, "exact": 0.53, "f1": 0.52, "baseline_f1": 0.23, "retrained_at": 1759287600000,
               "model": "Gradient Boosting", "devices": 4, "data_source": "simulated" },
  "learned": ["You usually get home around **16:30** on weekdays."],
  "devices": { "living_fan": { "status": "ready", "f1": 0.73, "baseline_f1": 0.12, "within_15": 0.83,
               "importance": { "temperature": 0.38, "time": 0.27, "habit": 0.2 }, "calibration": [[0.1, 0.08, 900]],
               "act": 0.7, "suggest": 0.5, "watts": 2.4, "kind": "thermal" } },
  "presence": { "status": "ready", "accuracy": 0.95, "arrival_weekday": "16:30" },
  "presence_now": { "home": false, "p_home_60": 0.86, "at": 1759300000000 },
  "energy": { "wasted_wh": 3.1, "saved_by_ai_wh": 4.8, "saved_by_rules_wh": 2.4, "wasted_by_ai_wh": 0.7, "ai_net_wh": 4.1 },
  "anomalies": [{ "device": "bedroom_fan", "kind": "high", "watts": 3.46, "usual": 1.68, "title": "Bedroom fan drawing 2.1x its usual power" }],
  "wear": {}, "stats": { "ai_on": 12, "ai_hit": 10, "ai_miss": 2, "smart_off": 7, "accepted": 3, "dismissed": 1, "expired": 4 } }
```

`data_source`: `simulated` (تاريخ مولّد لبداية النظام) · `mixed` · `real` — **اعرضه في الواجهة** حتى تكون الأرقام صادقة.

## `/summaries/{room}/{day}/{HH:MM}`

```json
{ "temp": 27.4, "hum": 48, "lux": 120, "occ": 1, "watts": 2.9, "dev": { "living_fan": 1, "living_light": 1 } }
```

`dev` يُستخدم لرسم "Last 24 hours" و"On today" في نافذة الجهاز.

## MQTT (داخل البيت فقط)

| Topic | الاتجاه | مثال |
|---|---|---|
| `home/<room>/<sensor>` | ESP32 → Pi | `home/living/temp` → `29.1` |
| `home/<room>/<device>/state` | ESP32 → Pi | `{"v":1,"level":70,"src":"button"}` |
| `home/<room>/<device>/set` | Pi → ESP32 | `{"v":1,"level":70}` أو `{"v":1,"mode":"Cool"}` |
| `home/<room>/<device>/power` | ESP32 → Pi | `{"w":2.9}` — قراءة INA226 (لكل الأجهزة، ومنها تُستنتج حالة أجهزة المراقبة، ويستخدمها الـ AI لكشف الأعطال والهدر) |
| `home/<room>/mmwave` و `home/<room>/pir` | ESP32 → Pi | `0`/`1` لكل حساس وجود على حدة (الإطفاء الذكي يحتاج الاثنين فارغين) |
| `home/door/event` | ESP32 → Pi | `{"type":"fingerprint","ok":true,"id":2}` |
| `home/door/open` | Pi → ESP32 | `{}` |
| `home/<node>/status` | LWT | `online` / `offline` (retained) |
| `home/<node>/config` | Pi → ESP32 | الجزء الخاص بالعقدة من `/config` (retained) |
| `home/<node>/config/ack` | ESP32 → Pi | `{"version":3}` → يُكتب في `/nodes/<node>/config_version` |

## SQLite على الـ Pi (مدخلات الـ AI — البيانات الخام لا تخرج من البيت)

جدول `readings(ts, key, value)` تكتبه خدمة MQTT ingest (والمحاكي). يكفي الكتابة عند التغيّر + نبضة كل 15 دقيقة.

| key | value |
|---|---|
| `<room>/temp` · `<room>/lux` · `<room>/occ` | الحرارة، الإضاءة، الوجود المدمج (0/1) |
| `<room>/mmwave` · `<room>/pir` | كل حساس وجود على حدة |
| `device/<id>` · `level/<id>` · `power/<id>` | الحالة 0/1، المستوى، الواط من INA226 |
| `override/<id>` | 1 لحظة أي تحكم يدوي (تطبيق أو زر) |
| `ai_miss/<id>` | 1 من تشغيل AI لم يحتجه أحد، 0 عند انتهائه (يكتبه الـ AI) |
| `mode/away` | 1 أثناء مشهد Away / السفر (لا يُستخدم في التدريب) |
| `door/entry` | 1 عند دخول شخص من الباب |
