# سياق المشروع الكامل (اقرأه أولاً)

> هذا الملف يلخّص كل القرارات التي اتُّخذت في المحادثة الأصلية مع Claude.
> **أي محادثة جديدة مع Claude تبدأ بقراءة هذا الملف** + `CLAUDE.md` + `docs/contract.md`.
> عند اتخاذ قرار جديد مهم: حدّث هذا الملف في نفس الـ commit.

## 1. ما هو المشروع
مشروع تخرج: **AI-Driven Adaptive Smart and Secure Home Ecosystem** — استوديو فيه غرفة نوم، صالة، مطبخ، حمام.
البيت يتحكم بالأجهزة يدوياً وبالقواعد وبالذكاء الاصطناعي، ويحمي الباب، ويقيس الطاقة، ويسجل كل عملية.
لغة التواصل مع الفريق: **العربية**. لغة الكود وواجهة التطبيق والـ commits: **الإنجليزية**.

## 2. روابط مهمة
| الشيء | الرابط |
|---|---|
| المستودع | https://github.com/anashroub2004/smart-home |
| خارطة طريق التنفيذ (64 خطوة: توصيل + برمجة + AI) | https://claude.ai/artifact/HTyrrd2Fz3QnSz7UFfw9zd |
| خطة تطبيق الويب (من Figma إلى الربط) | https://claude.ai/artifact/DLSs7LEhfDtJpiM7Eqg1Ek |
| التصميم التفاعلي (13 شاشة، المرجع البصري) | https://claude.ai/artifact/RYAjYb2ku2cXVdu7219Z9Q |

## 3. العتاد (قرارات نهائية)
- **المركز:** Raspberry Pi 8GB (MQTT broker + خدمات Python + SQLite + كاميرا + AI).
- **3 عقد ESP32:**
  - `living` — الصالة + المطبخ
  - `bedroom` — غرفة النوم + الحمام
  - `door` — الباب
- **الوجود:** 2× رادار C1001 mmWave (صالة، نوم) + 4× PIR داخلي + 1× PIR خارجي على GPIO17 في الـ Pi لتشغيل الكاميرا.
- **البيئة:** 2× SHT31 (0x44) + 2× BH1750 (0x23).
- **الطاقة:** INA226 (0x40/0x41/0x42) لكل جهاز — **لا PZEM ولا ACS712** (PZEM للـ AC فقط). Shunt R100 (~0.8 A)، R010 إن زاد تيار المحرك.
- **المشغّلات:** 3 محركات DC (مروحتان + غسالة) على MOSFET D4184 مع دايود 1N4007، ريليه للإضاءة، سيرفو MG996R للباب (تغذية 5V منفصلة).
- **الباب:** بصمة R503 + لوحة أرقام 4×4 + زر خروج + بازر + LED.
- **أزرار يدوية** على كل عقدة تعمل حتى بدون شبكة.

### خريطة الأطراف
- **عقد الغرف:**
  - I2C: 21/22
  - C1001: UART2 16/17
  - PIR: 34/35
  - مروحة MOSFET: 25
  - ريليه الضوء: 26
  - أزرار: 32/33
  - (غرفة النوم فقط) الغسالة: 27، وزرها: 14
- **عقدة الباب:**
  - R503: 16/17، والـ wakeup: 4
  - صفوف اللوحة: 13, 14, 27, 26
  - أعمدة اللوحة: 25, 33, 32, 23
  - سيرفو: 18
  - بازر: 19
  - LED: 5
  - زر خروج: 21

## 4. البرمجيات والبيانات
- **Firebase Realtime Database** = القاعدة الرئيسية للتطبيق. **SQLite على الـ Pi** = القراءات الخام والفيديو. الباب يعمل بدون إنترنت.
- **MQTT (Mosquitto)** داخل البيت فقط. ESP32 لا يعرف Firebase.
- **تطبيق ويب فقط** (لا تطبيق موبايل): Next.js 15 static export + Firebase Hosting + Firebase Auth. مستخدم واحد (owner). واجهة داكنة إنجليزية بخط Manrope.
- **عقد البيانات:** `docs/contract.md` هو المرجع الوحيد. الويب يعرف المسارات فقط من `web/lib/home.ts`.
- **نموذج القدرات (schema 3):** كل جهاز = `caps` (ماذا يستطيع: power write/read + energy كحد أدنى، و level/mode/status/lock اختيارياً) + `control` (app/button/rules/ai/confirm) + `rules` + `hw`. لا أنواع ثابتة في الكود. 10 قوالب + Custom في شاشة Add device، وAdd room. الدليل: `docs/ADDING_DEVICES.md`.
- **الأجهزة الحالية في المجسّم:** مروحتان (سرعة)، ضوءان، غسالة (app+button فقط)، الباب. التلفاز والثلاجة وغيرها **قوالب جاهزة فقط** لمن يطوّر المجسّم.
- **سجل كامل `/events`** لكل عملية: من (يدوي/AI/قاعدة/باب/نظام)، لماذا، التغيير، النتيجة، زمن الاستجابة.
- **المحاكي `pi/sim_house.py`** يتصرف تماماً مثل الـ Pi ويكتب عبر `pi/firebase_writer.py` (نفس الملف الذي سيستخدمه الـ Pi).
- **تبديل مصدر البيانات على مراحل** (الويب لا يتغير):
  1. المحاكيات + `sim_house.py`
  2. خدمات الـ Pi + `fake_nodes.py`
  3. ESP32 حقيقية
  4. Firebase الحقيقي

## 5. الذكاء الاصطناعي
- **النموذج:** `HistGradientBoostingClassifier` (scikit-learn)، نموذج لكل جهاز، يتنبأ بالحالة بعد 60 دقيقة. الملف: `ai/smart_home_ai.py`.
- **قائمة الأجهزة من `/config`:** كل جهاز `control.ai` يدخل تلقائياً، و"learning" حتى 21 يوماً من البيانات. أجهزة المراقبة في نفس الغرفة = معلومات إضافية (ctx_). مفاتيح SQLite: `<room>/temp|lux|occ` و `device/<id>`.
- **لماذا ليس أعقد:** البيانات جدولية وقليلة. قارنّا 4 نماذج (`ai/model_comparison_study.py`) والشبكة العصبية لم تتفوق. السقف بسبب عشوائية الإنسان.
- **الدقة على بيانات محاكاة:** 75% للتوقيت الدقيق، **93% ضمن ±15 دقيقة**.
- **سياسة القرار:**
  - ثقة ≥ 0.80 → تشغيل تلقائي
  - ثقة 0.60–0.80 → اقتراح للمستخدم
  - **لا يطفئ أبداً**؛ الإطفاء للقواعد (الغرفة فارغة)
  - تحكّم يدوي → يتوقف ساعتين عن ذلك الجهاز
- **التشغيل النهائي على الـ Pi:** تدريب ليلي 03:00 (cron)، تنبؤ كل 15 دقيقة، توقيت Asia/Hebron.

## 6. الحالة الحالية
### ✅ منجز
- تطبيق الويب كامل ومطابق للتصميم (Home, Room, Device sheet, Energy, Security, Insights, Activity, History, Settings, Add device, Login + التنبيه الأحمر).
- المحاكي كامل:
  - `--fast`
  - `--offline NODE`
  - `--fail-rate`
  - `--lockout`
  - `--reset`
- فحص تلقائي على GitHub Actions.

### ⏳ التالي (بالترتيب)
1. **ربط نموذج AI الحقيقي بالمحاكي:**
   - المحاكي يكتب قراءاته في SQLite.
   - `ai/` يتدرب ويكتب `/ai_schedule` و`/suggestions` و`/ai_insights` بدل المعادلة الوهمية داخل المحاكي.
   - إضافة تفسير القرارات.
   - الاقتراحات القديمة تنتهي بعد 15 دقيقة أو عند تغيّر الوضع.
2. Firebase الحقيقي: المشروع `ai-home-aef50` منشأ (Realtime Database في europe-west1 + Email/Password). الخطوات في `docs/DEPLOY.md`: رفع القواعد، تشغيل المحاكي بـ `--cloud`، النشر على https://ai-home-aef50.web.app.
3. خدمات الـ Pi الحقيقية:
   - `mqtt_ingest`
   - `commands`
   - `automation`
   - `security`
   - `camera`
   - `fake_nodes`
   - systemd
4. برمجة ESP32 (PlatformIO) حسب خريطة الأطراف.
5. اقتراحات أكاديمية اختيارية: تفسير قرارات AI، نموذج كشف أعطال الطاقة، التنبؤ بالوجود.

## 7. طريقة العمل
- **قبل العمل:** `git pull`.
- **بعده:** `git add .` ثم `git commit -m "..."` ثم `git pull` ثم `git push`.
- كل شخص يعمل على جزء مختلف (`web/` أو `pi/` أو `firmware/` أو `ai/`) لتجنب التعارض.
- تغيير شكل البيانات = تعديل `docs/contract.md` + `web/lib/contract.ts` + `pi/firebase_writer.py` معاً.
- **التشغيل المحلي:** 3 نوافذ:
  1. `firebase emulators:start`
  2. `python pi/sim_house.py --fast`
  3. `cd web` ثم `npm run dev`

  ثم افتح localhost:3000 وادخل بـ `owner@home.test` / `password123`.
- **مشاكل Windows معروفة:**
  - مسار فيه مسافة أو حروف عربية → ضع المشروع في `C:\dev\smart-home`.
  - `running scripts is disabled` → `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
  - المنفذ 9000 محجوز → `taskkill /F /IM java.exe` ثم أعد تشغيل Firebase.
- **ملفات التحديث من Claude (`.patch`):** تُحفظ بجانب مجلد المشروع وتُطبَّق بـ `git am ..\file.patch` ثم `git push`.
  - إن علق: `git am --abort`.
