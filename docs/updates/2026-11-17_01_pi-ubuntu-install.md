# تحديث 2026-11-17 (01) — تثبيت الـ hub على Raspberry Pi 5 (Ubuntu) وتشغيله

> **لكل محادثة أخرى في المشروع:** اقرأ هذا الملف والتزم بما فيه من الآن.
> التحديث السابق: `2026-11-12_01_mockup-hardware.md`

**من:** فارس (مع Claude)
**الجزء:** B الـ Pi
**الفرع / الـ commit:** `main` @ `92e8490` (لا تغيير في الكود، تثبيت وتشغيل فقط)

## ما الذي تغيّر
- ثُبّت Ubuntu Desktop (26.04.1 LTS، Python 3.14.4، aarch64) على Raspberry Pi 5 (اسم الجهاز `fares-desktop`، المستخدم `fares`).
- ثُبّتت الخدمات بـ `sudo bash pi/deploy/install.sh` إلى `/opt/smart-home`: أربع خدمات systemd (`smart-home-ingest`, `-commands`, `-automation`, `-security`) + `smart-home.target` + Mosquitto، وكلها تعمل عند الإقلاع.
- Mosquitto مقفل: المستخدمون `hub` و`bedroom` و`door` و`living`، وكلمات السر في `/etc/smart-home/mqtt-users.txt`، والـ ACL في `/etc/mosquitto/smart-home.acl`.
- مفتاح Firebase (`ai-home-aef50`) في `/etc/smart-home/ai-home-key.json`؛ ولّدناه من Firebase Console مباشرة على الـ Pi وحذفنا المفاتيح الزائدة.
- الـ AI تدرّب في نحو 72 ثانية على 18 أسبوعاً من التاريخ المحاكى (`scikit-learn==1.8.0`، `pandas 3.1.1`، `numpy 2.5.5`).
- التسجيل مغلق في Firebase Auth، ومراجعة Rules أُجريت (الافتراضي رفض، والقراءة `auth != null`).

## الملفات التي تغيّرت
| الملف | التغيير |
|---|---|
| (لا شيء في المستودع) | كل التغييرات إعدادات على جهاز الـ Pi |

## هل غيّرت شيئاً مشتركاً؟ (عقد البيانات، MQTT، seed، القواعد)
- لا.

## هل يؤثر على عمل الأجزاء الأخرى؟
| الجزء | التأثير |
|---|---|
| A الويب | الموقع https://ai-home-aef50.web.app يعرض عقداً وهمية حتى تتصل عقد حقيقية |
| B الـ Pi | الـ hub يعمل ويُعاد تشغيله تلقائياً |
| C الذكاء الاصطناعي | يعمل على الـ Pi ويعيد التدريب كل أربعاء 03:00 |
| D الـ ESP32 | العقد تتصل بـ Mosquitto على الـ Pi باسمها وكلمة سرها (لا Firebase مباشرة) |

## ما يجب تنفيذه بعد هذا التحديث
- حجز عنوان الـ Pi في الراوتر (DHCP reservation) للـ MAC `88:a2:9e:ed:2f:69`. العنوان الثابت `192.168.0.132/23` ضاع بعد فترة وأخذ الجهاز `192.168.0.50` عبر DHCP، فتوقفت العقد عن الاتصال بالعنوان القديم. حلّه مؤقتاً بـ `nmcli connection modify "WiFi" ipv4.method manual ipv4.addresses 192.168.0.132/23 ipv4.gateway 192.168.1.1`.
- شراء بطارية RTC ومصدر طاقة رسمي 27W (ظهر تحذير جهد منخفض مرة).
- مراقبة الخدمات 48 ساعة.

## قرارات جديدة (تُنسخ أيضاً إلى PROJECT_CONTEXT.md)
- العقد تتصل بالـ hub عبر MQTT على المنفذ 1883 فقط، وعنوان الـ Pi يجب أن يكون ثابتاً (حجز في الراوتر).
- أول بدء لـ `smart-home-ingest` فشل مرة بسبب `database is locked` ثم نجح بعد إعادة تشغيل systemd؛ يُقترح WAL و`busy_timeout` في `ai/store.py` (لم يُنفَّذ بعد).

## التالي في هذا الجزء
- تثبيت عنوان الـ Pi، ثم فحص `journalctl -u smart-home-ingest` مع وصول عقدة حقيقية.
- قواعد Firebase بحسب الدور (بدل `auth != null` لكل المستخدمين).
