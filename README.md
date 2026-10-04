# Smart Home — مشروع التخرج

منزل ذكي: Raspberry Pi + 3 عقد ESP32 + تطبيق ويب (Next.js) + Firebase + ذكاء اصطناعي (Gradient Boosting).

```
smart-home/
├─ web/          تطبيق الويب (Next.js 15 + Tailwind + Firebase)
├─ pi/           خدمات الـ Raspberry Pi + محاكي البيت (sim_house.py)
├─ ai/           نموذج الذكاء الاصطناعي (تدريب + تنبؤ)
├─ firmware/     كود الـ ESP32 (لاحقاً)
├─ docs/         عقد البيانات (contract.md) + الإعدادات الأولية (seed.json)
├─ firebase.json         إعداد Firebase والمحاكيات (emulators)
├─ database.rules.json   قواعد أمان قاعدة البيانات
└─ .github/workflows/    فحص تلقائي لكل push
```

📌 **كل القرارات وحالة المشروع:** `docs/PROJECT_CONTEXT.md` — اقرأه أولاً.

**أهم قاعدة:** `docs/contract.md` هو المرجع. الويب لا يعرف مسارات Firebase إلا من ملف واحد: `web/lib/home.ts`.

---

## 1) المتطلبات (مرة واحدة على جهازك)

| الأداة | الإصدار | التحقق |
|---|---|---|
| Git | أي إصدار حديث | `git --version` |
| Node.js | 20 أو أحدث | `node -v` |
| Python | 3.10 أو أحدث | `python --version` |
| Java (JDK) | 21 | `java -version` — تحتاجه محاكيات Firebase فقط |
| Firebase CLI | أحدث إصدار | `npm install -g firebase-tools` ثم `firebase --version` |
| VS Code | — | افتح المجلد وثبّت الإضافات المقترحة عندما يطلب ذلك |

> المحاكيات تعمل بمشروع اسمه `demo-smart-home` — **لا تحتاج حساب Firebase ولا تسجيل دخول** أثناء التطوير.

## 2) أول تشغيل

```bash
git clone https://github.com/<USERNAME>/smart-home.git
cd smart-home/web
npm install            # يُنشئ package-lock.json — اعمل له commit أول مرة
cd ..
```

## 3) التشغيل اليومي — 3 طرفيات

```bash
# الطرفية 1 — قاعدة البيانات + تسجيل الدخول (محلياً)
firebase emulators:start

# الطرفية 2 — البيت الوهمي (يتصرف مثل الـ Pi تماماً)
python pi/sim_house.py --fast

# الطرفية 3 — تطبيق الويب
cd web
npm run dev
```

| الرابط | ماذا فيه |
|---|---|
| http://localhost:3000 | تطبيق الويب — الدخول: `owner@home.test` / `password123` |
| http://localhost:4000 | واجهة المحاكيات: شاهد البيانات تتغير لحظياً في Realtime Database |

جرّب: `python pi/sim_house.py --offline bedroom` أو `--fail-rate 0.3` لترى كيف تتعامل الواجهة مع الأعطال.

## 4) ربط المشروع بـ GitHub (مرة واحدة)

1. أنشئ مستودعاً فارغاً على GitHub باسم `smart-home` (بدون README وبدون .gitignore).
2. من داخل مجلد المشروع:

```bash
git remote add origin https://github.com/<USERNAME>/smart-home.git
git branch -M main
git push -u origin main
```

3. ادعُ أعضاء الفريق: **Settings → Collaborators → Add people**.
4. بعد كل push، تبويب **Actions** يبني الويب ويفحص كود Python تلقائياً. ✓ أخضر = كل شيء سليم.

## 5) طريقة عمل الفريق

```bash
git checkout main && git pull            # ابدأ دائماً من آخر نسخة
git checkout -b feature/energy-page      # فرع لكل مهمة
# ... عدّل ...
git add . && git commit -m "Energy: weekly chart"
git push -u origin feature/energy-page   # ثم افتح Pull Request على GitHub
```

- لا تكتب مباشرة على `main` — كل شيء عبر Pull Request، وانتظر علامة CI الخضراء.
- غيّرت شكل البيانات؟ عدّل `docs/contract.md` و `web/lib/contract.ts` و `pi/firebase_writer.py` **في نفس الـ commit**.
- لا ترفع أسراراً أبداً: `.env.local` و `service-account*.json` مستبعدة تلقائياً.

## 6) مراحل تبديل مصدر البيانات (الويب لا يتغير)

| المرحلة | مصدر البيانات | ماذا يتغير |
|---|---|---|
| 1 — الآن | المحاكيات + `sim_house.py` | لا شيء |
| 2 | خدمات الـ Pi + `fake_nodes.py` (ESP32 وهمية عبر MQTT) | تشغيل خدمات `pi/` بدل المحاكي |
| 3 | ESP32 حقيقية | رفع `firmware/` على القطع |
| 4 | Firebase الحقيقي | `web/.env.local`: `NEXT_PUBLIC_USE_EMULATOR=0` + مفاتيح المشروع، ثم `firebase deploy` |

## 7) النشر (عند الجاهزية)

```bash
firebase login
firebase use --add            # اختر مشروعك الحقيقي
cd web && npm run build && cd ..
firebase deploy --only hosting,database
```

## 8) الذكاء الاصطناعي

راجع `ai/README_AR.md`. للتجربة بدون عتاد:

```bash
cd ai
pip install -r requirements.txt
python smart_home_ai.py demo && python smart_home_ai.py train && python smart_home_ai.py predict
```

> `demo` يمسح جدول `readings` في `home.db` داخل مجلد `ai/` — لا تشغله على قاعدة بيانات الـ Pi الحقيقية.
