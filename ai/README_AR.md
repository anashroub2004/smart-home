# خدمة الذكاء الاصطناعي (Gradient Boosting)

تعمل على الـ Raspberry Pi، وتتنبأ بحالة كل جهاز **بعد 60 دقيقة**.

## التثبيت على الـ Raspberry Pi

```bash
python3 -m venv ~/ai-env
source ~/ai-env/bin/activate
pip install -r requirements.txt
```

## التجربة بدون عتاد

```bash
python smart_home_ai.py demo      # يولّد 10 أسابيع من البيانات المحاكاة في home.db
python smart_home_ai.py train     # يدرب ويقيّم ويحفظ نموذجاً لكل جهاز في models/
python smart_home_ai.py predict   # يطبع قرارات الساعة القادمة
```

## التشغيل التلقائي (crontab -e)

```
0 3 * * *     cd ~/ai_service && ~/ai-env/bin/python smart_home_ai.py train
*/15 * * * *  cd ~/ai_service && ~/ai-env/bin/python smart_home_ai.py predict
```

## ما تحتاجه الخدمة من باقي النظام

خدمة الـ MQTT ingest تكتب كل قراءة في جدول `readings` بالشكل: `(ts, key, value)`

| key | value |
|---|---|
| `bedroom/temp`, `living/temp` | الحرارة من SHT31 |
| `bedroom/lux`, `living/lux` | الإضاءة من BH1750 |
| `bedroom/occ`, `living/occ` | وجود شخص (C1001 + PIR) = 0 أو 1 |
| `bedroom/fan`, `living/fan`, `bedroom/light`, `living/light` | حالة الجهاز = 0 أو 1 |
| `override/<device>` | يُكتب عند أي تحكم يدوي من المستخدم |

## ما تُرجعه الخدمة

| action | المعنى | إلى أين يُرسل |
|---|---|---|
| `schedule_on` | ثقة 80%+ أن الجهاز سيُستخدم، شغّله عند `execute_at` | MQTT ← خدمة الأتمتة |
| `suggest_on` / `suggest_off` | ثقة متوسطة، اسأل المستخدم | Firebase ← تطبيق الويب |
| `paused_by_override` | المستخدم تحكم يدوياً خلال آخر ساعتين | لا شيء |

الذكاء الاصطناعي **لا يطفئ أي جهاز تلقائياً**. الإطفاء عند هدر الطاقة تقوم به القواعد (جهاز شغال + غرفة فارغة).
