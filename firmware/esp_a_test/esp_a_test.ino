// ESP-A (node "bedroom") bring-up test - Arduino IDE, board: ESP32 Dev Module
// Libraries: PubSubClient, DHT sensor library (Adafruit) + Adafruit Unified Sensor, ESP32Servo
// Serial Monitor: 115200
#include <WiFi.h>
#include <Wire.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ESP32Servo.h>

// ---------- EDIT THESE ----------
const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST = "192.168.0.132";
const char* MQTT_USER = "bedroom";
const char* MQTT_PASS = "PASSWORD_FROM_/etc/smart-home/mqtt-users.txt";
const float SHUNT_OHMS = 0.1;      // check your INA226 board (many are 0.1, some 0.002)
// ---------- PINS (GPIO numbers = the number after D or P on the board) ----------
#define PIN_DHT   4
#define PIN_FC51  34
#define PIN_LD_RX 16   // ESP RX2  <- LD2410C TX
#define PIN_LD_TX 17   // ESP TX2  -> LD2410C RX
#define PIN_LED1  25
#define PIN_LED2  26
#define PIN_BTN1  32
#define PIN_BTN2  33
#define PIN_BTN3  27
#define PIN_SERVO 18
#define SDA_PIN 21
#define SCL_PIN 22

DHT dht(PIN_DHT, DHT11);
Servo servo;
WiFiClient wifi;
PubSubClient mqtt(wifi);
HardwareSerial LD(2);

bool led1=false, led2=false;
bool lastB[3]={1,1,1};
uint32_t tSensors=0, tScan=0;
int ldState=0;                  // 0 none, 1 moving, 2 static, 3 both
uint32_t ldLastFrame=0;

// ---------- I2C helpers ----------
bool i2cHas(uint8_t a){ Wire.beginTransmission(a); return Wire.endTransmission()==0; }
uint16_t inaRead(uint8_t a,uint8_t reg){
  Wire.beginTransmission(a); Wire.write(reg); Wire.endTransmission(false);
  Wire.requestFrom(a,(uint8_t)2); uint16_t v=Wire.read()<<8; v|=Wire.read(); return v;
}
float bh1750Lux(){
  Wire.beginTransmission(0x23); Wire.write(0x10); if(Wire.endTransmission()!=0) return NAN; // continuous high-res
  delay(180);
  Wire.requestFrom((uint8_t)0x23,(uint8_t)2); if(Wire.available()<2) return NAN;
  uint16_t raw=Wire.read()<<8; raw|=Wire.read(); return raw/1.2;
}
void i2cScan(){
  Serial.print("I2C found:");
  int n=0; for(uint8_t a=1;a<127;a++) if(i2cHas(a)){ Serial.printf(" 0x%02X",a); n++; }
  Serial.printf("  (%d)  expected: 0x23 BH1750, 0x40 INA226#1, 0x41 INA226#2\n",n);
}
void inaReport(uint8_t a,const char* name){
  if(!i2cHas(a)){ Serial.printf("%s 0x%02X: NOT FOUND\n",name,a); return; }
  float busV=inaRead(a,0x02)*0.00125;
  int16_t sh=(int16_t)inaRead(a,0x01); float shV=sh*0.0000025;
  float amps=shV/SHUNT_OHMS;
  Serial.printf("%s 0x%02X: bus=%.2fV  shunt=%.3fmV  I=%.3fA  P=%.2fW\n",name,a,busV,shV*1000,amps,busV*amps);
}

// ---------- LD2410C minimal frame parser ----------
void ldPoll(){
  static uint8_t buf[64]; static int n=0;
  while(LD.available()){
    uint8_t b=LD.read();
    if(n<64) buf[n++]=b;
    if(n>=4 && !(buf[0]==0xF4&&buf[1]==0xF3&&buf[2]==0xF2&&buf[3]==0xF1)){ // resync
      memmove(buf,buf+1,--n); continue; }
    if(n>=6){
      int len=buf[4]|(buf[5]<<8);
      if(len>40){ n=0; continue; }
      if(n>=10+len){
        if(len>=3 && buf[6]==0x02 && buf[7]==0xAA){ ldState=buf[8]; ldLastFrame=millis(); }
        n=0;
      }
    }
  }
}

// ---------- MQTT ----------
void pub(const char* t,const String& v,bool retain=true){ if(mqtt.connected()) mqtt.publish(t,v.c_str(),retain); }
void connectWifi(){
  if(WiFi.status()==WL_CONNECTED) return;
  Serial.printf("WiFi %s ...",WIFI_SSID); WiFi.begin(WIFI_SSID,WIFI_PASS);
  for(int i=0;i<40&&WiFi.status()!=WL_CONNECTED;i++){ delay(500); Serial.print('.'); }
  Serial.println(WiFi.status()==WL_CONNECTED? String(" OK ")+WiFi.localIP().toString() : " FAILED");
}
void connectMqtt(){
  if(mqtt.connected()||WiFi.status()!=WL_CONNECTED) return;
  mqtt.setServer(MQTT_HOST,1883);
  Serial.printf("MQTT host=%s user='%s' pass='%s' (len %d) ... ",MQTT_HOST,MQTT_USER,MQTT_PASS,(int)strlen(MQTT_PASS));
  // last-will: home/bedroom/status = offline (retained)
  if(mqtt.connect("esp-bedroom",MQTT_USER,MQTT_PASS,"home/bedroom/status",1,true,"offline")){
    mqtt.publish("home/bedroom/status","online",true); Serial.println(" connected");
  } else Serial.printf(" failed rc=%d (4=bad user/pass, 5=not authorised)\n",mqtt.state());
  delay(3000);
}

void setup(){
  Serial.begin(115200); delay(300);
  Serial.println("\n=== ESP-A bring-up test ===");
  pinMode(PIN_LED1,OUTPUT); pinMode(PIN_LED2,OUTPUT);
  pinMode(PIN_BTN1,INPUT_PULLUP); pinMode(PIN_BTN2,INPUT_PULLUP); pinMode(PIN_BTN3,INPUT_PULLUP);
  pinMode(PIN_FC51,INPUT);
  Wire.begin(SDA_PIN,SCL_PIN);
  dht.begin();
  LD.begin(256000,SERIAL_8N1,PIN_LD_RX,PIN_LD_TX);
  servo.attach(PIN_SERVO);
  // quick LED + servo self-test
  for(int i=0;i<2;i++){ digitalWrite(PIN_LED1,1); digitalWrite(PIN_LED2,1); delay(250); digitalWrite(PIN_LED1,0); digitalWrite(PIN_LED2,0); delay(250); }
  servo.write(0); delay(500); servo.write(90); delay(500); servo.write(0);
  i2cScan();
  connectWifi(); connectMqtt();
  Serial.println("Press BTN1/BTN2 = toggle LED1/LED2, BTN3 = move servo.");
}

void loop(){
  connectWifi(); connectMqtt(); mqtt.loop(); ldPoll();

  // buttons (pressed = LOW)
  int pins[3]={PIN_BTN1,PIN_BTN2,PIN_BTN3};
  for(int i=0;i<3;i++){
    bool v=digitalRead(pins[i]);
    if(lastB[i]==1 && v==0){
      Serial.printf("BTN%d pressed\n",i+1);
      if(i==0){ led1=!led1; digitalWrite(PIN_LED1,led1); }
      if(i==1){ led2=!led2; digitalWrite(PIN_LED2,led2); }
      if(i==2){ static bool up=false; up=!up; servo.write(up?90:0); }
      delay(30);
    }
    lastB[i]=v;
  }

  // sensors every 3 s
  if(millis()-tSensors>3000){
    tSensors=millis();
    float t=dht.readTemperature(), h=dht.readHumidity();
    float lux=bh1750Lux();
    int fc=digitalRead(PIN_FC51);   // FC-51: usually LOW = obstacle
    bool ldAlive = millis()-ldLastFrame<2000;
    Serial.printf("DHT11 t=%.1fC h=%.0f%% | BH1750 %.0f lux | FC-51=%d | LD2410 %s state=%d\n",
      t,h,lux,fc, ldAlive?"OK":"NO DATA", ldState);
    inaReport(0x40,"INA226 #1"); inaReport(0x41,"INA226 #2");
    if(!isnan(t)) pub("home/bedroom/temp",String(t,1));
    if(!isnan(h)) pub("home/bedroom/hum",String(h,0));
    if(!isnan(lux)) pub("home/bedroom/lux",String(lux,0));
    if(ldAlive){ pub("home/bedroom/mmwave",String(ldState!=0)); pub("home/bedroom/pir",String((ldState&1)?1:0)); }
  }
  if(millis()-tScan>15000){ tScan=millis(); i2cScan(); }
}
