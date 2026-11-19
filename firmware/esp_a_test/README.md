# esp_a_test — ESP-A (node `bedroom`) bring-up sketch

Arduino IDE, board **ESP32 Dev Module**, Serial Monitor 115200.
Libraries: PubSubClient, DHT sensor library (Adafruit) + Adafruit Unified Sensor, ESP32Servo.

Before uploading edit the top of the file: `WIFI_SSID`, `WIFI_PASS`, `MQTT_HOST` (the Pi's current IP), `MQTT_PASS`
(the `bedroom` line of `/etc/smart-home/mqtt-users.txt` on the Pi). **Never commit real passwords.**

It scans I2C (BH1750 0x23, INA226 0x40/0x41), reads DHT11 / FC-51 / LD2410C, toggles LEDs and the servo from the buttons,
and publishes `home/bedroom/{temp,hum,lux,mmwave,pir}` + `home/bedroom/status` (LWT) per `docs/contract.md`.
Pins (GPIO numbers): DHT 4, FC-51 34, LD2410C RX/TX 16/17, LED 25/26, buttons 32/33/27, servo 18, I2C 21/22.
This is a wiring test, not the final firmware (no `config` / `set` handling).
