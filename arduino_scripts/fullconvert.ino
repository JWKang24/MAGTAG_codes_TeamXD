#include <WiFi.h>                 // Wi-Fi for ESP-NOW and AP
#include <esp_now.h>              // ESP-NOW communication
#include <Preferences.h>          // NVS storage
#include <Adafruit_EPD.h>         // ThinkInk display
#include <Adafruit_NeoPixel.h>    // NeoPixel LEDs
#include <WebServer.h>            // Survey HTTP server
#include <qrcode.h>               // QR code generation

// --------- Pins ----------
#define BUTTON_15 15
#define BUTTON_14 14
#define BUTTON_12 12
#define BUTTON_11 11
#define NEOPIXEL_PIN 21

// --------- Constants ----------
#define MAX_PEERS 32
#define MAX_INTERESTS 12
#define BROADCAST_INTERVAL 2000
#define RSSI_BADGE_THRESHOLD -65
#define SURVEY_PORT 80

// ThinkInk 2.9" grayscale display pins
#define EPD_DC      7
#define EPD_CS      8
#define EPD_BUSY    -1
#define SRAM_CS     -1
#define EPD_RESET   6

// Create display and LEDs
ThinkInk_290_Grayscale4_EAAMFGN display(EPD_DC, EPD_RESET, EPD_CS, SRAM_CS, EPD_BUSY);
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
enum DeviceMode { MODE_SEARCH = 0, MODE_CHAT = 1 };
DeviceMode currentMode = MODE_SEARCH;

// --------- Peers ----------
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

// --------- Helpers ----------
void flashBadgeMatch() {
  for (int i = 0; i < 2; i++) {
    pixels.fill(pixels.Color(0, 80, 80));
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

// Build ESP-NOW message
String buildMessage() {
  String interests = "";
  for (int i = 0; i < interestCount; i++) {
    interests += myInterests[i];
    if (i < interestCount - 1) interests += ",";
  }
  return String((int)currentMode) + "|" + myName + "|" + interests + "||||0|0";
}

// --------- ESP-NOW RX ----------
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

// --------- Display ---------
void renderDisplay() {
  static DeviceMode lastMode = MODE_SEARCH;
  static int lastPeerCount = -1;
  if (currentMode == lastMode && peerCount == lastPeerCount) return; // no change

  lastMode = currentMode;
  lastPeerCount = peerCount;

  display.begin();
  display.clearBuffer();
  display.setTextColor(EPD_BLACK);
  display.setCursor(4, 16);
  display.print(currentMode == MODE_SEARCH ? "SEARCH" : "CHAT");
  display.setCursor(4, 36);
  display.print("Nearby: "); display.print(peerCount);
  display.display();
}

// --------- QR Code Display ---------
void displayQRCode(const char* url) {
  QRCode qrcode;
  uint8_t qrcodeData[qrcode_getBufferSize(3)];
  qrcode_initText(&qrcode, qrcodeData, 3, ECC_LOW, url);

  int scale = 2;
  display.clearBuffer();
  for (uint8_t y = 0; y < qrcode.size; y++) {
    for (uint8_t x = 0; x < qrcode.size; x++) {
      if (qrcode_getModule(&qrcode, x, y)) {
        display.fillRect(x*scale, y*scale, scale, scale, EPD_BLACK);
      }
    }
  }
  display.display();
}

// --------- Survey HTTP page ---------
String buildSurveyPage() {
  String html = "<html><body>";
  html += "<h2>Badge Setup</h2>";
  html += "<form method='POST'>";
  html += "Name: <input name='name' value='" + myName + "'><br>";
  for (int i = 0; i < MAX_INTERESTS; i++) {
    html += "<input type='text' name='interest" + String(i) + "' value='";
    if (i < interestCount) html += myInterests[i];
    html += "'><br>";
  }
  html += "<input type='submit' value='Save'>";
  html += "</form></body></html>";
  return html;
}

// Handle survey
void handleSurvey() {
  if (server.method() == HTTP_POST) {
    myName = server.arg("name");
    interestCount = 0;
    for (int i = 0; i < MAX_INTERESTS; i++) {
      String val = server.arg("interest" + String(i));
      if (val.length() > 0) myInterests[interestCount++] = val;
    }

    prefs.begin("cfg", false);
    prefs.putString("name", myName);
    for (int i = 0; i < interestCount; i++) {
      String key = "interest" + String(i);
      prefs.putString(key.c_str(), myInterests[i]);
    }
    prefs.putBool("marker", true); // survey done marker
    prefs.end();
    surveyComplete = true;
  }
  server.send(200, "text/html", buildSurveyPage());
}

// --------- Setup ---------
void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(BUTTON_15, INPUT_PULLUP);
  pinMode(BUTTON_14, INPUT_PULLUP);
  pinMode(BUTTON_12, INPUT_PULLUP);
  pinMode(BUTTON_11, INPUT_PULLUP);

  pixels.begin(); pixels.setBrightness(40); pixels.clear(); pixels.show();

  // Load preferences
  prefs.begin("cfg", true);
  myName = prefs.getString("name", "MagTag");
  espnowChannel = prefs.getInt("channel", 6);
  interestCount = 0;
  for (int i = 0; i < MAX_INTERESTS; i++) {
    String v = prefs.getString(("interest" + String(i)).c_str(), "");
    if (v.length() > 0) myInterests[interestCount++] = v;
  }
  bool markerExists = prefs.getBool("marker", false);
  prefs.end();

  if (!markerExists) {
    // Start survey
    WiFi.mode(WIFI_AP);
    WiFi.softAP("MagTag Survey");

    server.on("/", handleSurvey);
    server.begin();

    displayQRCode("http://192.168.4.1"); // show QR for AP survey
    while (!surveyComplete) server.handleClient();
  }

  // ESP-NOW setup
  WiFi.mode(WIFI_STA);
  esp_now_init();
  esp_now_register_recv_cb(onReceive);

  uint8_t broadcastMac[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, broadcastMac, 6);
  peer.channel = espnowChannel;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  renderDisplay();
}

// --------- Loop ---------
unsigned long lastBroadcast = 0;
void loop() {
  if (millis() - lastBroadcast > BROADCAST_INTERVAL) {
    String msg = buildMessage();
    esp_now_send((uint8_t*)"\xff\xff\xff\xff\xff\xff", (uint8_t*)msg.c_str(), msg.length());
    lastBroadcast = millis();
  }

  if (!digitalRead(BUTTON_15)) {
    currentMode = (currentMode == MODE_SEARCH) ? MODE_CHAT : MODE_SEARCH;
    renderDisplay();
    waitRelease(BUTTON_15);
  }

  if (!digitalRead(BUTTON_12)) {
    badgeVisible = !badgeVisible;
    renderDisplay();
    waitRelease(BUTTON_12);
  }
}
