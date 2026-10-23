# pi/ — خدمات الـ Raspberry Pi + المحاكي

| الملف | الدور |
|---|---|
| `firebase_writer.py` | **كل** كتابة إلى Firebase تمر من هنا (المحاكي والـ Pi يستخدمان نفس الملف) |
| `rest_db.py` | عميل بسيط لـ Realtime Database عبر REST (مكتبة Python القياسية فقط) |
| `sim_house.py` | محاكي البيت: يتصرف تماماً مثل الـ Pi حتى نبني الويب قبل وصول القطع |
| `fake_nodes.py` | 3 عقد ESP32 وهمية على MQTT (`living`، `bedroom`، `door`) تتكلم رسائل `docs/contract.md` بالضبط. الغرف والحساسات والأجهزة من `/config`. تُستبدل بالعقد الحقيقية واحدة واحدة |
| `mqtt_ingest.py` | **خدمة Pi** (1b): MQTT ← SQLite (للـ AI) + `/home_state` + `/nodes` + `/events` (زر الجدار، عقدة offline)، ويرسل لكل عقدة جزأها من `/config` |
| `commands.py` | **خدمة Pi** (1c): `/commands` من التطبيق ← `set` للعقدة ← ينتظر ردها 5 ثوانٍ ← done / failed + History. بلا إعادة محاولة تلقائية |
| `automation.py` | **خدمة Pi** (1d): العقل. القواعد (1d-1) + الـ AI الحقيقي (1d-2: plan كل 15 د، tick كل 5 ث، الأسئلة، التدريب 03:00، نماذج `ai/models/home`). يرى البيت بنفسه عبر MQTT، وينفّذ بـ `src: rule` / `ai`، ويحترم التحكم اليدوي و`/ai_pause`. خيارات: `--no-ai`، `--train-now`، `--ask DEVICE` |
| `world.py` | **العالم**: أحمد (روتين `ai/personas`)، وما تراه الحساسات (حضور، حرارة ورطوبة بقصور الغرفة، ضوء النهار)، وما يفعله بيده (أزرار، أجهزة من خطة يومه). مصدر واحد لـ `sim_house.py` و`fake_nodes.py --world` |
| `house_state.py` | "صورة البيت" من رسائل العقد (من في الغرفة، حرارة، أجهزة) — نفس الكود لـ `mqtt_ingest` و`automation` |
| `mqtt_exec.py` | `MqttExecutor`: "أرسل `set` وانتظر رد العقدة 5 ثوانٍ" — لـ `commands` و`automation` |
| `house_words.py` | كلمات History المشتركة بين الخدمات ("Living room fan"، "On · Medium") |

## المرحلة 1: العقد الوهمية + خدمات الـ Pi على الحاسوب

```bash
# نافذة 1: Firebase المحلي
firebase emulators:start --only auth,database
# نافذة 2: العقد الوهمية — مع أحمد (روتينه، الحرارة، الإضاءة، أزراره) من pi/world.py
python pi/fake_nodes.py --world
#   (بدون --world: شخص عشوائي بسيط. help: press، move، routine، hotter، finger، unplug، mute ...)
# نافذة 3: الأذن — تملأ /config وحساب owner في Firebase فارغ
python pi/mqtt_ingest.py
# نافذة 4: اليد
python pi/commands.py
# نافذة 5: العقل (القواعد) — إيقافه لا يوقف البيت
python pi/automation.py
# نافذة 6: التطبيق ← http://localhost:3000  (owner@home.test / password123)
cd web; npm run dev
```

## العقد الوهمية وحدها

يحتاج Mosquitto (على Windows: `winget install --id EclipseFoundation.Mosquitto --source winget`، يعمل كخدمة تلقائياً) و`pip install "paho-mqtt>=2.0,<3"`.

```bash
# نافذة 1: كل ما تقوله العقد
"C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t "home/#" -v

# نافذة 2: العقد (اكتب help لترى الأوامر: press، move، finger، unplug ...)
python pi/fake_nodes.py
python pi/fake_nodes.py --only living --broken bathroom/temp
```

## تشغيل المحاكي

```bash
# الطرفية 1 (من جذر المشروع)
firebase emulators:start

# الطرفية 2
python pi/sim_house.py --fast
```

| الخيار | ماذا يحاكي |
|---|---|
| `--fast` | كل شيء أسرع 10 مرات (القواعد، الملخصات، الذكاء الاصطناعي، أحداث الباب) |
| `--offline living` | عقدة الصالة والمطبخ مفصولة — الأوامر لها تفشل والواجهة تعرض "Offline" |
| `--lockout` | 5 محاولات PIN خاطئة فوراً — تظهر شاشة التنبيه الحمراء |
| `--fail-rate 0.3` | 30% من الأوامر تفشل — لاختبار حالات الخطأ في الواجهة |
| `--reset` | يمسح قاعدة البيانات ويعيد التعبئة من `docs/seed.json` |

## الخدمات القادمة (حسب خارطة الطريق)

عند وصول القطع نضيف هنا بنفس النمط، وكلها تستخدم `firebase_writer.py`:

```
mqtt_ingest.py      MQTT  -> SQLite readings + /home_state
commands.py         /commands -> MQTT .../set   (نفس منطق handle_commands في المحاكي)
automation.py       القواعد + تنفيذ ai_schedule
security.py         الباب، access_log، alerts
camera.py           PIR الخارجي -> تسجيل فيديو على الـ Pi
fake_nodes.py       يحاكي ESP32 على MQTT (المرحلة 2 من تبديل مصدر البيانات)
systemd/*.service   تشغيل كل خدمة تلقائياً
```
