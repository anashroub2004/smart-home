# pi/ — خدمات الـ Raspberry Pi + المحاكي

| الملف | الدور |
|---|---|
| `firebase_writer.py` | **كل** كتابة إلى Firebase تمر من هنا (المحاكي والـ Pi يستخدمان نفس الملف) |
| `rest_db.py` | عميل بسيط لـ Realtime Database عبر REST (مكتبة Python القياسية فقط) |
| `sim_house.py` | محاكي البيت: يتصرف تماماً مثل الـ Pi حتى نبني الويب قبل وصول القطع |

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
| `--offline bedroom` | عقدة غرفة النوم مفصولة — الأوامر لها تفشل والواجهة تعرض "offline" |
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
