# pi/ — خدمات الـ Raspberry Pi + المحاكي

| الملف | الدور |
|---|---|
| `firebase_writer.py` | **كل** كتابة إلى Firebase تمر من هنا (المحاكي والـ Pi يستخدمان نفس الملف) |
| `rest_db.py` | عميل بسيط لـ Realtime Database عبر REST (مكتبة Python القياسية فقط) |
| `sim_house.py` | محاكي البيت: يتصرف تماماً مثل الـ Pi حتى نبني الويب قبل وصول القطع |
| `fake_nodes.py` | 3 عقد ESP32 وهمية على MQTT (`living`، `bedroom`، `door`) تتكلم رسائل `docs/contract.md` بالضبط. الغرف والحساسات والأجهزة من `/config`. تُستبدل بالعقد الحقيقية واحدة واحدة |
| `mqtt_ingest.py` | **خدمة Pi** (1b): MQTT ← SQLite (للـ AI) + `/home_state` (مع `base_w` 6.8 W) + `/nodes` + `/events` (زر الجدار، عقدة offline)، ويرسل لكل عقدة جزأها من `/config`. وكل دقيقة: `/summaries` (رسوم التطبيق) و`/energy_daily` (شاشة Energy، يكمل بعد إعادة التشغيل، يوم جديد في منتصف الليل) |
| `commands.py` | **خدمة Pi** (1c): `/commands` من التطبيق ← `set` للعقدة ← ينتظر ردها 5 ثوانٍ ← done / failed + History. بلا إعادة محاولة تلقائية |
| `automation.py` | **خدمة Pi** (1d): العقل. القواعد (1d-1) + الـ AI الحقيقي (1d-2: plan كل 15 د، tick كل 5 ث، الأسئلة، التدريب 03:00، نماذج `ai/models/home`). يرى البيت بنفسه عبر MQTT، وينفّذ بـ `src: rule` / `ai`، ويحترم التحكم اليدوي و`/ai_pause`. خيارات: `--no-ai`، `--train-now`، `--ask DEVICE` |
| `security.py` | **خدمة Pi** (ب): دفتر الباب. يسمع `home/door/event` ← `/access_log` + History + التنبيه الأصفر (أول خطأ) والأحمر (كل قفل) + `/home_state/door`. **كل شيء في SQLite أولاً** (جدول `access`) ثم Firebase، فانقطاع الإنترنت لا يضيّع شيئاً (يُرسل لاحقاً بوقته الأصلي). يبدأ بلا إنترنت بآخر `/config` محفوظ |
| `door_guard.py` | عقل عقدة الباب (مرجع الـ firmware): 5 أخطاء ← قفل 60 ث، ثم 120، 240 … حتى 900. أثناء القفل: لا رقم سري، والبصمة المعروفة وزر الخروج والتطبيق تفتح. يتذكر بعد انقطاع الكهرباء. يستعمله `fake_nodes.py` و`sim_house.py` |
| `world.py` | **العالم**: أحمد (روتين `ai/personas`)، وما تراه الحساسات (حضور، حرارة ورطوبة بقصور الغرفة، ضوء النهار)، وما يفعله بيده (أزرار، أجهزة من خطة يومه). مصدر واحد لـ `sim_house.py` و`fake_nodes.py --world` |
| `scenario_bridge.py`، `run_scenarios.py` | **للاختبار فقط:** سيناريوهات التوأم العشرة على الخدمات الحقيقية (بوابة المرحلة 1) |
| `hub_clock.py` | **للاختبار فقط:** الساعة المسرّعة المشتركة (`hub/clock`) — العالم ينشرها، والخدمات بـ `--sim-clock` تقرؤها، والـ AI يوقفها أثناء التدريب |
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
# نافذة 6: دفتر الباب (الباب يحرس نفسه بدونه؛ هذا يسجّل وينبّه)
python pi/security.py
# نافذة 7: التطبيق ← http://localhost:3000  (owner@home.test / password123)
cd web; npm run dev
```

### يوم كامل في 24 دقيقة (ساعة مشتركة ×60، للاختبار فقط)

العالم يملك الساعة وينشرها على `hub/clock`، والخدمات تقرؤها بـ `--sim-clock` (`pi/hub_clock.py`). ابدأ العالم **أولاً**، واستعمل قاعدة بيانات منفصلة لأن الوقت المسرّع يسبق الحقيقي:

```bash
python pi/fake_nodes.py --world --speed 60          # أوامر: time، jump 21:00، speed 120، pause، resume
python pi/mqtt_ingest.py --sim-clock --db ai/data/home_sim.db
python pi/commands.py --sim-clock --db ai/data/home_sim.db
python pi/automation.py --sim-clock --db ai/data/home_sim.db     # نماذج ai/models/home_sim
```
أثناء تدريب الـ AI (أول تشغيل، و03:00 كل ليلة) يطلب `automation` إيقاف الساعة، فيتوقف يوم أحمد كما في التوأم.

### السيناريوهات العشرة على الخدمات الحقيقية (بوابة المرحلة 1)

مع النوافذ الأربع أعلاه + `commands` (ينفّذ "نعم" على أسئلة الـ AI)، وبعد "AI ready":

```bash
python pi/run_scenarios.py                         # العشرة — المتوقع: 10 / 10 PASS
python pi/run_scenarios.py forgot_light guard_hit  # بعضها
```
نفس `pi/scenarios.py` الذي يستعمله التوأم، يعمل داخل `automation` عبر `pi/scenario_bridge.py`: العالم يتحرك عبر `hub/test/world`، والقرارات تمر بالطريق الحقيقي (MQTT ← `automation` ← `set` ← العقدة)، وإجاباتك على الأسئلة تُكتب في Firebase كما يفعل التطبيق.

### الباب (اختبر الأمان)

في كونسول `fake_nodes.py`: `pin wrong` خمس مرات ← قفل 60 ث، `pin ok` أثناء القفل يُرفض، `finger 1` يفتح ويصفّر، `door` يعرض حالة الحارس.
ذاكرة الباب الدائمة: `pi/.flash/door.json` (احذفه لإعادة ضبط المصنع). اختبارات: `python -m unittest discover -s pi/tests`

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
camera.py           PIR الخارجي -> تسجيل فيديو على الـ Pi
fake_nodes.py       يحاكي ESP32 على MQTT (المرحلة 2 من تبديل مصدر البيانات)
systemd/*.service   تشغيل كل خدمة تلقائياً
```
