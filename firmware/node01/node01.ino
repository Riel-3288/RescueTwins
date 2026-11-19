#include <WiFi.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_BME280.h>
#include <ld2410.h>
#include <time.h>
#include <sys/time.h>  

// ---- enable ONE of these later (change 0 -> 1) ----
#define USE_HTTP  0   // oneM2M HTTP to ACME (old method, disabled)
#define USE_MQTT  1   // [MQTT] oneM2M MQTT binding (now ON)

#if USE_HTTP
  #include <HTTPClient.h>
#endif
#if USE_MQTT
  #include <PubSubClient.h>
  #include <Preferences.h>   // [MQTT] boot counter for unique rqi
#endif

// ================= NODE CONFIG =================
const char* DEVICE_ID = "node-01";
const char* ZONE_ID   = "A";          // "A" or "B"
const char* SOURCE    = "physical";   // "physical" or "simulation"

#define MQ2_PIN  34   // smoke_raw (ADC1, via 10k/20k divider on AO)
#define MQ7_PIN  35   // co_raw    (ADC1, via 10k/20k divider on AO)
#define READ_MS  5000

// ================= WiFi CONFIG =================
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// ============ oneM2M HTTP CONFIG ([ACME]) - disabled ============
#if USE_HTTP
const char* BASE_URL = "http://<MN_CSE_IP>:8001/~/id-mn-zone-a/mn-zone-a";
const char* AE_RN    = "node-01";
const char* ORIGIN   = "CNode01";
const char* RVI      = "4";
#endif

// ============ oneM2M MQTT CONFIG ============   // [MQTT] values from Zihan
#if USE_MQTT
const char*    MQTT_HOST   = "<MQTT_BROKER_IP>";
const uint16_t MQTT_PORT   = 1883;
const char*    MQTT_USER   = "node-01";
const char*    MQTT_PASS   = "YOUR_MQTT_PASSWORD";  
const char*    MQTT_CLIENT = "rescuetwin-node-01"; 
const char*    AE_ORIG     = "CNode01";
const char*    CSE_ID      = "id-mn-zone-a";
const char*    CSE_PATH    = "/id-mn-zone-a/mn-zone-a";
const char*    AE_NAME     = "node-01";

String TOPIC_REQ  = String("/oneM2M/req/")  + AE_ORIG + "/" + CSE_ID + "/json";
String TOPIC_RESP = String("/oneM2M/resp/") + AE_ORIG + "/" + CSE_ID + "/json";

WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);
uint32_t bootNo = 0, okCount = 0, failCount = 0;
#endif
// ===========================================================

Adafruit_BME280 bme;
ld2410 radar;

bool bmeOK = false, radarOK = false;
unsigned long lastRead = 0;
uint32_t rqiCount = 0;
uint32_t seqNo = 0;  

struct Readings {
  float temp = NAN, hum = NAN, press = NAN;
  int smoke = -1, co = -1;
  bool radarOK = false, presence = false;
};

// ---------- helpers ----------
String utcNow() {
  time_t now = time(nullptr);
  if (now < 1700000000) return "";             // clock not synced yet
  struct tm t;
  gmtime_r(&now, &t);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(buf);
}

// [LAT] UTC time in ms since 1970; 0 if NTP not synced yet
uint64_t epochMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) return 0;
  return (uint64_t)tv.tv_sec * 1000ULL + tv.tv_usec / 1000;
}

float r1(float x) { return isnan(x) ? x : roundf(x * 10) / 10; }
bool inRange(float x, float lo, float hi) { return !isnan(x) && x >= lo && x <= hi; }
bool mvOK(int v) { return v > 50 && v < 3200; }   // MQ reading in millivolts

// ---------- 1. readSensors() ----------
Readings readSensors() {
  Readings r;

  // --- BME280 ---
  if (!bmeOK) bmeOK = bme.begin(0x76) || bme.begin(0x77);   // retry if lost
  if (bmeOK) {
    r.temp  = bme.readTemperature();
    r.hum   = bme.readHumidity();
    r.press = bme.readPressure() / 100.0F;

    // same chip: if any value is impossible, distrust all three and re-init next cycle
    if (!inRange(r.temp, -40, 85) || !inRange(r.press, 300, 1100) || !inRange(r.hum, 0.1, 100)) {
      Serial.println("[BME] garbage reading -> re-init");
      r.temp = r.hum = r.press = NAN;
      bmeOK = false;
    }
  }

  // --- MQ sensors (millivolts at the pin, after the divider) ---
  r.smoke = analogReadMilliVolts(MQ2_PIN);
  r.co    = analogReadMilliVolts(MQ7_PIN);
  
  // --- LD2410 ---
  if (!radarOK) radarOK = radar.begin(Serial2);              // retry if lost

  unsigned long t0 = millis();                               // [RADAR] read for up to 200 ms
  while (millis() - t0 < 200) {                              // [RADAR] to get a fresh frame
    radar.read();
    if (radar.isConnected()) break;
    delay(5);
  }

    if (radarOK && radar.isConnected()) {
    r.radarOK  = true;
    r.presence = radar.movingTargetDetected() ||              // [RADAR] anything moving, or
                 (radar.stationaryTargetDetected() &&         // [RADAR] something still
                  radar.stationaryTargetDistance() < 150);    // [RADAR] within 1.5 m
  }

  if (r.radarOK) {                                           // [RADAR] debug
    Serial.printf("[RADAR] move=%d %ucm e=%u | still=%d %ucm e=%u\n",
      radar.movingTargetDetected(),     radar.movingTargetDistance(),     radar.movingTargetEnergy(),
      radar.stationaryTargetDetected(), radar.stationaryTargetDistance(), radar.stationaryTargetEnergy());
  } else {
    Serial.println("[RADAR] no fresh frame");                // [RADAR] debug
  }

  return r;
}

// ---------- 2. buildJSON() ----------
void fillHead(JsonObject o, const char* type) {
  o["device_id"]   = DEVICE_ID;
  o["zone_id"]     = ZONE_ID;
  o["sensor_type"] = type;
}
void fillTail(JsonObject o, const char* unit, bool valid) {
  o["unit"]    = unit;
  o["source"]  = SOURCE;
  o["quality"] = valid ? "valid" : "invalid";
  String ts = utcNow();
  if (ts.length()) o["observed_at"] = ts;
  else             o["observed_at"] = nullptr;
  o["seq"] = seqNo;  
}
void buildJSON(JsonObject o, const char* type, float v, const char* unit, bool valid) {
  fillHead(o, type); o["value"] = r1(v); fillTail(o, unit, valid);
}
void buildJSON(JsonObject o, const char* type, int v, const char* unit, bool valid) {
  fillHead(o, type); o["value"] = v; fillTail(o, unit, valid);
}
void buildJSON(JsonObject o, const char* type, bool v, const char* unit, bool valid) {
  fillHead(o, type); o["value"] = v; fillTail(o, unit, valid);
}

// ---------- 3. uploadData() ----------
#if USE_HTTP
// oneM2M HTTP POST, ty=4 (contentInstance)
void uploadData(JsonDocument& reading) {
  if (WiFi.status() != WL_CONNECTED) return;

  const char* type = reading["sensor_type"];
  String url = String(BASE_URL) + "/" + AE_RN + "/" + type;   // <BASE_URL>/node-01/temperature

  JsonDocument cin;
  cin["m2m:cin"]["con"] = reading;
  String body;
  serializeJson(cin, body);

  HTTPClient http;
  http.begin(url);
  http.setTimeout(3000);
  http.addHeader("X-M2M-Origin", ORIGIN);
  http.addHeader("X-M2M-RI", "node01-" + String(++rqiCount));   // new ID every request
  http.addHeader("X-M2M-RVI", RVI);
  http.addHeader("Content-Type", "application/json;ty=4");
  http.addHeader("Accept", "application/json");
  const char* keys[] = {"X-M2M-RSC"};
  http.collectHeaders(keys, 1);

  int code = http.POST(body);
  if (code < 0) {
    Serial.printf("[HTTP] %s ERROR %s\n", type, http.errorToString(code).c_str());
  } else {
    Serial.printf("[HTTP] %s code=%d rsc=%s\n", type, code, http.header("X-M2M-RSC").c_str());
    if (code >= 400) Serial.println(http.getString());   // ACME error message
  }
  http.end();
}

#elif USE_MQTT
// [MQTT] ---- whole section replaced ----

// CSE reply arrives here. Only rsc 2001 = record created in ACME.
void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument resp;
  if (deserializeJson(resp, payload, len)) { Serial.println("[CSE] bad response JSON"); return; }
  int rsc = resp["rsc"] | 0;
  const char* rqi = resp["rqi"] | "?";
  if (rsc == 2001) { okCount++;   Serial.printf("[CSE] OK   rsc=2001 rqi=%s (ok=%lu)\n", rqi, (unsigned long)okCount); }
  else             { failCount++; Serial.printf("[CSE] FAIL rsc=%d rqi=%s (fail=%lu)\n", rsc, rqi, (unsigned long)failCount); }
}

bool mqttConnect() {
  if (mqtt.connected()) return true;
  if (WiFi.status() != WL_CONNECTED) return false;
  Serial.printf("[MQTT] connecting %s:%u ... ", MQTT_HOST, MQTT_PORT);
  if (!mqtt.connect(MQTT_CLIENT, MQTT_USER, MQTT_PASS)) {
    Serial.printf("failed, state=%d\n", mqtt.state());
    return false;
  }
  Serial.println("OK");
  mqtt.subscribe(TOPIC_RESP.c_str());          // subscribe BEFORE any publish
  Serial.printf("[MQTT] subscribed %s\n", TOPIC_RESP.c_str());
  return true;
}

void mqttSetup() {
  Preferences p; p.begin("rt", false);
  bootNo = p.getUInt("boot", 0) + 1; p.putUInt("boot", bootNo); p.end();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(1024);                    // default 256 is too small for the request
  mqtt.setSocketTimeout(5);
  mqtt.setCallback(onMqttMessage);
  mqttConnect();
}

void mqttLoop() {
  static unsigned long lastTry = 0;
  if (!mqtt.connected() && millis() - lastTry > 5000) { lastTry = millis(); mqttConnect(); }
  mqtt.loop();                                 // needed to receive CSE responses
}

void uploadData(JsonDocument& reading) {
  if (!mqtt.connected()) { Serial.println("[MQTT] offline, reading not sent"); return; }

  const char* type = reading["sensor_type"];
  char rqi[48];
  snprintf(rqi, sizeof(rqi), "node-01-boot%lu-%06lu", (unsigned long)bootNo, (unsigned long)++rqiCount);
  uint64_t ms = epochMs();                            // [LAT] stamp just before send
  if (ms) reading["ts_ms"] = ms; else reading["ts_ms"] = nullptr;

  String con;                                  // con must be a JSON *string*
  serializeJson(reading, con);

  JsonDocument req;
  req["op"]  = 1;
  req["to"]  = String(CSE_PATH) + "/" + AE_NAME + "/" + type;   // .../node-01/temperature
  req["fr"]  = AE_ORIG;
  req["rqi"] = rqi;
  req["rvi"] = "4";
  req["ty"]  = 4;
  req["pc"]["m2m:cin"]["con"] = con;

  String out;
  serializeJson(req, out);
  bool ok = mqtt.publish(TOPIC_REQ.c_str(), out.c_str());
  Serial.printf("[MQTT] %s %s -> %s seq=%lu ts=%llu\n", ok ? "sent" : "PUBLISH FAILED",
              rqi, type, (unsigned long)seqNo, (unsigned long long)ms);   // [LAT]
}

#else
// Test mode: nothing is uploaded, readings only print to Serial
void uploadData(JsonDocument& reading) {}
#endif

// ---------- WiFi with diagnostics ----------
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
    Serial.printf("[WiFi] disconnected, reason=%d\n", info.wifi_sta_disconnected.reason);
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP)
    Serial.printf("[WiFi] got IP %s\n", WiFi.localIP().toString().c_str());
}

void connectWiFi() {
  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);   // limit current spikes (before scan)
  WiFi.disconnect(true,true);
  delay(100);

  int n = WiFi.scanNetworks();
  Serial.printf("Found %d networks:\n", n);
  bool seen = false;
  for (int i = 0; i < n; i++) {
    Serial.printf("  '%s'  ch%d  %d dBm  auth=%d\n",
                  WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i), WiFi.encryptionType(i));
    if (WiFi.SSID(i) == WIFI_SSID) seen = true;
  }
  Serial.println(seen ? ">> Your SSID IS visible" : ">> Your SSID NOT found (5GHz? name mismatch?)");

  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 20000) {
    delay(500);
    Serial.printf("status=%d\n", WiFi.status());
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("WiFi OK, IP %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.printf("WiFi FAILED (status=%d)\n", WiFi.status());
}

// ---------- send one reading ----------
void publishReading(JsonDocument& doc) {
  serializeJson(doc, Serial);
  Serial.println();
  uploadData(doc);
}

void sendAll(const Readings& r) {
  seqNo++;  
  JsonDocument d;

  d.clear(); buildJSON(d.to<JsonObject>(), "temperature", r.temp,  "degC",    inRange(r.temp, -40, 85));    publishReading(d);
  d.clear(); buildJSON(d.to<JsonObject>(), "humidity",    r.hum,   "percent", inRange(r.hum, 0, 100));      publishReading(d);
  d.clear(); buildJSON(d.to<JsonObject>(), "pressure",    r.press, "hPa",     inRange(r.press, 300, 1100)); publishReading(d);

  d.clear(); buildJSON(d.to<JsonObject>(), "smoke_raw", r.smoke, "mV", mvOK(r.smoke)); publishReading(d);
  d.clear(); buildJSON(d.to<JsonObject>(), "co_raw",    r.co,    "mV", mvOK(r.co));    publishReading(d);

  d.clear(); buildJSON(d.to<JsonObject>(), "presence", r.presence, "boolean", r.radarOK); publishReading(d);
  Serial.println("---");
}

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(MQ2_PIN, INPUT);
  pinMode(MQ7_PIN, INPUT);

  Wire.begin(21, 22);
  bmeOK = bme.begin(0x76) || bme.begin(0x77);
  Serial.println(bmeOK ? "BME280 OK" : "BME280 NOT FOUND");

  Serial2.setRxBufferSize(1024); //radar bigger buffer
  Serial2.begin(256000, SERIAL_8N1, 16, 17);
  radarOK = radar.begin(Serial2);
  Serial.println(radarOK ? "LD2410 OK" : "LD2410 NOT RESPONDING");

  connectWiFi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");   // UTC for observed_at
   if (WiFi.status() == WL_CONNECTED) {
    unsigned long t0 = millis();
    while (time(nullptr) < 1700000000 && millis() - t0 < 10000) delay(200);
    Serial.println(time(nullptr) >= 1700000000 ? "NTP synced" : "NTP NOT synced");
  }

#if USE_MQTT
  mqttSetup();                                           // [MQTT]
#endif
}

void loop() {
  radar.read();                                          // call often

#if USE_MQTT
  mqttLoop();                                            // [MQTT] reconnect + receive responses
#endif

  if (millis() - lastRead >= READ_MS) {
    lastRead = millis();
    Readings r = readSensors();
    sendAll(r);
  }
}