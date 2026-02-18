#include <WiFi.h>
#include <esp_now.h>
#include <Preferences.h>
#include <Adafruit_ThinkInk.h>
#include <Adafruit_NeoPixel.h>
#include <WebServer.h>

// --------- Pins ----------
#define BUTTON_A 15
#define BUTTON_B 14
#define BUTTON_D 11
#define NEOPIXEL_PIN 21

// --------- Constants ----------
#define MAX_PEERS 32
#define MAX_INTERESTS 12
#define MAX_MSG_LEN 250
#define BROADCAST_INTERVAL 2000
#define PEER_TIMEOUT 15000
#define RSSI_BADGE_THRESHOLD -65
#define SURVEY_PORT 80

// MagTag 2.9" grayscale display pins
#define EPD_DC      7
#define EPD_CS      8
#define EPD_BUSY    -1
#define SRAM_CS     -1
#define EPD_RESET   6

// Create ThinkInk display object
ThinkInk_290_Grayscale4_EAAMFGN display(EPD_DC, EPD_RESET, EPD_CS, SRAM_CS, EPD_BUSY);

// --------- LEDs ----------
Adafruit_NeoPixel pixels(4, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

// --------- State ----------
Preferences prefs;
bool surveyComplete = false;
bool badgeVisible = false;
String myName = "MagTag";
String myInterests[MAX_INTERESTS];
int interestCount = 0;
int espnowChannel = 6;

// --------- Modes ----------
enum Mode { MODE_SEARCH = 0, MODE_CHAT = 1 };
Mode currentMode = MODE_SEARCH;

// --------- Peer Struct ----------
struct PeerInfo {
  uint8_t mac[6];
  String name;
  String interests[MAX_INTERESTS];
  int interestCount;
  int rssi;
  unsigned long lastSeen;
};
PeerInfo peers[MAX_PEERS];
int peerCount = 0;

// --------- Web server ----------
WebServer server(SURVEY_PORT);

// --------- Marker for boot ----------
bool markerExists = false;

// --------- Helpers ----------
String buildMessage() {
  String interests = "";
  for (int i = 0; i < interestCount; i++) {
    interests += myInterests[i];
    if (i < interestCount - 1) interests += ",";
  }
  String msg = String((int)currentMode) + "|" + myName.substring(0,20) + "|" + interests + "||||0|0";
  return msg.substring(0, MAX_MSG_LEN);
}

void flashBadgeMatch() {
  for (int i = 0; i < 2; i++) {
    pixels.fill(pixels.Color(0,80,80));
    pixels.show();
    delay(80);
    pixels.clear();
    pixels.show();
    delay(80);
  }
}

void waitRelease(int pin) {
  while (!digitalRead(pin)) delay(30);
}

// --------- ESP-NOW RX callback ----------
void onReceive(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  String msg = String((char*)data).substring(0, len);
  int rssi = info->rx_ctrl->rssi;
  bool found = false;
  for (int i = 0; i < peerCount; i++) {
    if (!memcmp(peers[i].mac, info->src_addr, 6)) {
      peers[i].rssi = rssi;
      peers[i].lastSeen = millis();
      found = true;
      break;
    }
  }
  if (!found && peerCount < MAX_PEERS) {
    memcpy(peers[peerCount].mac, info->src_addr, 6);
    peers[peerCount].rssi = rssi;
    peers[peerCount].lastSeen = millis();
    peerCount++;
    if (rssi > RSSI_BADGE_THRESHOLD) flashBadgeMatch();
  }
}

// --------- Display ----------
void renderDisplay() {
  display.begin();
  display.clearBuffer();
  display.setTextColor(EPD_BLACK);
  display.setCursor(4, 16);
  display.print(currentMode == MODE_SEARCH ? "SEARCH" : "CHAT");
  display.setCursor(4, 36);
  display.print("Nearby: "); 
  display.print(peerCount);
  display.display();
}

// --------- Survey Page ----------
String buildSurveyPage() {
  String html = "<html><body><h2>Badge Setup</h2><form method='POST'>";
  html += "Name: <input name='name' value='" + myName + "'><br>";
  for (int i = 0; i < MAX_INTERESTS; i++) {
    html += "<input type='text' name='interest" + String(i) + "' value='";
    if (i < interestCount) html += myInterests[i];
    html += "'><br>";
  }
  html += "<input type='submit' value='Save'></form></body></html>";
  return html;
}

void handleSurvey() {
  if (server.method() == HTTP_POST) {
    myName = server.arg("name");
    interestCount = 0;
    for (int i = 0; i < MAX_INTERESTS; i++) {
      String val = server.arg("interest" + String(i));
      if (val.length() > 0) myInterests[interestCount++] = val;
    }
    prefs.begin("cfg", false);
    prefs.putString("name", myName.c_str()); // FIX: convert key to const char*
    for (int i = 0; i < interestCount; i++) {
      String key = "interest" + String(i);
      prefs.putString(key.c_str(), myInterests[i]); // FIX
    }
    prefs.end();
    surveyComplete = true;
    markerExists = true;
  }
  server.send(200, "text/html", buildSurveyPage());
}

// --------- Setup ----------
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(BUTTON_A, INPUT_PULLUP);
  pinMode(BUTTON_B, INPUT_PULLUP);
  pinMode(BUTTON_D, INPUT_PULLUP);

  pixels.begin();
  pixels.setBrightness(40);
  pixels.clear();
  pixels.show();

  // Load preferences
  prefs.begin("cfg", true);
  myName = prefs.getString("name", "MagTag");
  espnowChannel = prefs.getInt("channel", 6);
  for (int i = 0; i < MAX_INTERESTS; i++) {
    String v = prefs.getString(("interest" + String(i)).c_str(), "");
    if (v.length() > 0) myInterests[interestCount++] = v;
  }
  markerExists = prefs.getBool("marker", false);
  prefs.end();

  if (!markerExists) {
    WiFi.mode(WIFI_AP);
    WiFi.softAP("MagTag Survey");
    server.on("/", handleSurvey);
    server.begin();
    renderDisplay();
    while (!surveyComplete) {
      server.handleClient();
      renderDisplay();
      delay(50);
    }
    prefs.begin("cfg", false);
    prefs.putBool("marker", true);
    prefs.end();
  }

  // --------- ESP-NOW ----------
  WiFi.mode(WIFI_STA);
  WiFi.setChannel(espnowChannel); // FIX: Arduino ESP32

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_register_recv_cb(onReceive);

  uint8_t broadcastMac[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, broadcastMac, 6);
  peer.channel = espnowChannel;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  renderDisplay();
}

// --------- Loop ----------
unsigned long lastBroadcast = 0;
void loop() {
  if (millis() - lastBroadcast > BROADCAST_INTERVAL) {
    String msg = buildMessage();
    uint8_t broadcastMac[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
    esp_now_send(broadcastMac, (uint8_t*)msg.c_str(), msg.length());
    lastBroadcast = millis();
  }

  if (!digitalRead(BUTTON_A)) {
    currentMode = (currentMode == MODE_SEARCH) ? MODE_CHAT : MODE_SEARCH;
    renderDisplay();
    waitRelease(BUTTON_A);
  }

  if (!digitalRead(BUTTON_D)) {
    badgeVisible = !badgeVisible;
    renderDisplay();
    waitRelease(BUTTON_D);
  }

  renderDisplay();
  delay(120);
}
