# عقد البيانات (Data Contract)

هذا الملف هو **المرجع الوحيد** لشكل البيانات بين: الـ Pi، المحاكي، تطبيق الويب، والذكاء الاصطناعي.
أي تغيير هنا يجب أن ينعكس في: `web/lib/contract.ts` و `pi/firebase_writer.py`.

القاعدة: **الويب يكتب فقط في `/commands` و`/ai_pause` و`/suggestions/{id}` و`/alerts/{id}/ack` و`/config`**. كل شيء آخر يكتبه الـ Pi (أو المحاكي).

## مسارات Firebase Realtime Database

| المسار | من يكتب | الوصف |
|---|---|---|
| `/config` | الويب (owner) | الغرف والأجهزة والعتبات — مصدر رسم الواجهة |
| `/home_state` | Pi | الحالة الحية لكل غرفة وجهاز |
| `/nodes/{node}` | Pi | `online`, `last_seen`, `config_version`, `rssi` |
| `/commands/{device}` | الويب ← Pi | أمر واحد معلّق لكل جهاز |
| `/events/{pushId}` | Pi فقط | سجل كامل لكل عملية في البيت |
| `/summaries/{room}/{YYYY-MM-DD}/{HH:MM}` | Pi | ملخص كل دقيقة (حرارة/إضاءة/وجود/واط) |
| `/energy_daily/{YYYY-MM-DD}/{device}` | Pi | Wh لكل جهاز في اليوم |
| `/ai_schedule/{device}` | Pi (AI) | قرارات التشغيل المجدولة |
| `/ai_pause/{device}` | الويب | رقم = إيقاف الأتمتة (AI + القواعد) لهذا الجهاز حتى هذا الوقت (ms). حذفه = استئناف |
| `/suggestions/{id}` | Pi (AI) / الويب يرد | اقتراحات بثقة متوسطة |
| `/access_log/{pushId}` | Pi | محاولات فتح الباب |
| `/alerts/{pushId}` | Pi | تنبيهات حرجة |
| `/users/{uid}` | يدوي | `{ role: "owner", name }` |

## `/home_state`

```json
{
  "updated_at": 1759300000000,
  "power_w": 17.4,
  "rooms": {
    "living": { "temp": 29.1, "hum": 48, "lux": 320, "occ": 1, "motion_at": 1759299990000 }
  },
  "devices": {
    "living_fan": { "v": 1, "speed": 70, "src": "web", "at": 1759299000000, "watts": 4.2 },
    "door_lock":  { "v": 0, "src": "keypad", "at": 1759290000000 }
  }
}
```

`v`: 1 = يعمل/مفتوح، 0 = مطفأ/مقفل. `speed` للمراوح 0–100. كل الأوقات **ms منذ 1970 (UTC)**.

## `/commands/{device}`

```json
{ "v": 1, "speed": 70, "by": "<uid>", "at": 1759300000000, "status": "pending" }
```

الويب يكتب `status: "pending"`. الـ Pi ينفذ ثم يغيّر إلى `done` أو `failed` (+ `error`).
إن لم يتغير خلال 10 ثوانٍ، الويب يعرض "لم يستجب الجهاز".

## `/events/{pushId}` — السجل الكامل

```json
{
  "at": 1759300000123,
  "kind": "device",
  "device": "living_fan",
  "room": "living",
  "source": "web",
  "by": "owner",
  "trigger": "manual",
  "confidence": null,
  "from": { "v": 0 },
  "to":   { "v": 1, "speed": 70 },
  "result": "ok",
  "latency_ms": 412,
  "tags": ["manual"],
  "text": "Ceiling fan turned on (70%)"
}
```

| الحقل | القيم |
|---|---|
| `kind` | `device` · `door` · `motion` · `node` · `ai` · `rule` · `scene` · `alert` · `config` |
| `source` | `web` · `button` · `ai` · `rule` · `keypad` · `fingerprint` · `exit_button` · `system` |
| `trigger` | `manual` · `ai_schedule` · `ai_suggestion` · `empty_room` · `low_lux` · `scene:<id>` · `override` … |
| `result` | `ok` · `failed` · `denied` |

## MQTT (داخل البيت فقط)

| Topic | الاتجاه | مثال |
|---|---|---|
| `home/<room>/<sensor>` | ESP32 → Pi | `home/living/temp` → `29.1` |
| `home/<room>/<device>/state` | ESP32 → Pi | `{"v":1,"speed":70,"src":"button"}` |
| `home/<room>/<device>/set` | Pi → ESP32 | `{"v":1,"speed":70}` |
| `home/door/event` | ESP32 → Pi | `{"type":"fingerprint","ok":true,"id":3}` |
| `home/door/open` | Pi → ESP32 | `{}` |
| `home/<node>/status` | LWT | `online` / `offline` (retained) |
| `home/<node>/config` | Pi → ESP32 | الجزء الخاص بالعقدة من `/config` (retained) |
| `home/<node>/config/ack` | ESP32 → Pi | `{"version":3}` |
