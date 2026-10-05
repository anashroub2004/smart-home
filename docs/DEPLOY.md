# التشغيل على Firebase الحقيقي

المشروع الحقيقي: **`ai-home-aef50`** (الاسم: ai home، الخطة المجانية Spark)

| | التطوير اليومي | الحقيقي |
|---|---|---|
| قاعدة البيانات | المحاكي المحلي (`firebase emulators:start`) | `https://ai-home-aef50-default-rtdb.europe-west1.firebasedatabase.app` |
| الدخول | `owner@home.test` / `password123` | الحساب الذي أنشأته في Firebase → Authentication |
| رابط التطبيق | http://localhost:3000 | https://ai-home-aef50.web.app |

المحاكيات تبقى كما هي — كل ما في هذا الملف **إضافي**.

## مرة واحدة على كل جهاز

```powershell
firebase login
pip install -r pi/requirements.txt
```

ومفتاح الخدمة (Service account) يُحفظ **خارج** المشروع، مثلاً: `C:\dev\secrets\ai-home-key.json`
(Firebase console ← Project settings ← Service accounts ← Generate new private key).
**لا يُرفع على GitHub ولا يُرسل لأحد.**

## 1) رفع قواعد الأمان

```powershell
firebase deploy --only database --project prod
```

كرّرها كلما تغيّر `database.rules.json`.

## 2) تعبئة القاعدة الحقيقية وتشغيل البيت الوهمي عليها

أول مرة (مع UID حساب المالك من Authentication → Users):

```powershell
python pi/sim_house.py --cloud C:\dev\secrets\ai-home-key.json --owner-uid <UID> --fast
```

بعدها:

```powershell
python pi/sim_house.py --cloud C:\dev\secrets\ai-home-key.json --fast
```

- يقرأ ويكتب في القاعدة الحقيقية بدل المحاكي — نفس ما سيفعله الـ Pi لاحقاً.
- ⚠️ الخطة المجانية لها حد تنزيل شهري (10 GB). المحاكي يقلل الاستعلامات في هذا الوضع، لكن **لا تتركه يعمل 24 ساعة** — شغّله للعرض والتجربة فقط.
- `--reset` يمسح القاعدة الحقيقية بالكامل (يطلب تأكيد YES).

## 3) نشر تطبيق الويب

```powershell
cd web
npm run build
cd ..
firebase deploy --only hosting --project prod
```

`npm run build` يستخدم `web/.env.production` تلقائياً فيتصل بالمشروع الحقيقي.
الرابط: **https://ai-home-aef50.web.app** — أرسله للفريق والمشرف.

## (اختياري) `npm run dev` على القاعدة الحقيقية

انسخ `web/.env.cloud-dev.example` إلى `web/.env.local` وأعد تشغيل `npm run dev`. احذف `.env.local` للعودة للمحاكي.

## الشركاء

Firebase console ← Project settings ← **Users and permissions** ← Add member ← دور **Editor**.
