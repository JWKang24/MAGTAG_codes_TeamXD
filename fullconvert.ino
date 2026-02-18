#include <WiFi.h>                 // For Wi-Fi connectivity
#include <esp_now.h>              // For ESP-NOW communication
#include <Preferences.h>          // For NVS storage
#include <Adafruit_ThinkInk.h>    // For MagTag ThinkInk display
#include <Adafruit_NeoPixel.h>    // For NeoPixel LED control
#include <WebServer.h>            // For survey web page

// --------- Pins ----------
#define BUTTON_MODE   15  // D15 - toggle search/chat mode
#define BUTTON_SURVEY 14  // D14 - enter survey / pairing
#define BUTTON_UNUSED 12  // D12 - reserved / optional
#define BUTTON_BADGE  11  // D11 - toggle badge display
#define NEOPIXEL_PIN  21  // NeoPixel data pin

// --------- Constants ----------
#define MAX_PEERS 32
#define MAX_INTERESTS 12
#define MAX_MSG_LEN 250
#define BROADCAST_INTERVAL 2000
#define PEER_TIMEOUT 15000
#define RSSI_BADGE_THRESHOLD -65
#define SURVEY_PORT 80

// --------- Display pins (MagTag 2.9") ----------
#define EPD_DC      7
#define EPD_CS      8
#define EPD_BUSY    -1
#define SRAM_CS     -1
#define EPD_RESET   6

// --------- Display object ----------
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

// --------- Marker for survey ----------
#define MARKER_KEY "marker"
bool markerExists = false;

// --------- Helpers ----------

// Build ESP-NOW message
String buildMessage() {
  String interests = "";
  for (int i = 0; i < interestCount; i++) {
    interests += myInterests[i];
    if (i < interestCount - 1) interests += ",";
  }
  String msg = String((int)currentMode) + "|" + myName.substring(0,20) + "|" + interests + "||||0|0";
  return msg.substring(0, MAX_MSG_LEN);
}

// Flash NeoPixels for badge match
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

// Wait for button release
void waitRelease(int pin) {
  while (!digitalRead(pin)) delay(30);
}

// --------- ESP-NOW receive callback ----------
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
Mode lastMode = MODE_SEARCH;
int lastPeerCount = -1;
bool lastBadgeVisible = false;

void renderDisplay() {
  if (currentMode != lastMode || peerCount != lastPeerCount || badgeVisible != lastBadgeVisible) {
    display.begin();
    display.clearBuffer();
    display.setTextColor(EPD_BLACK);
    display.setCursor(4, 16);
    display.print(currentMode == MODE_SEARCH ? "SEARCH" : "CHAT");
    display.setCursor(4, 36);
    display.print("Nearby: ");
    display.print(peerCount);
    display.display();

    lastMode = currentMode;
    lastPeerCount = peerCount;
    lastBadgeVisible = badgeVisible;
  }
}

// --------- Survey page ----------
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
      prefs.putString("interest" + String(i), myInterests[i]);
    }
    prefs.putBool(MARKER_KEY, true);
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

  pinMode(BUTTON_MODE, INPUT_PULLUP);
  pinMode(BUTTON_SURVEY, INPUT_PULLUP);
  pinMode(BUTTON_UNUSED, INPUT_PULLUP);
  pinMode(BUTTON_BADGE, INPUT_PULLUP);

  pixels.begin();
  pixels.setBrightness(40);
  pixels.clear();
  pixels.show();

  // Load preferences
  prefs.begin("cfg", true);
  myName = prefs.getString("name", "MagTag");
  espnowChannel = prefs.getInt("channel", 6);
  for (int i = 0; i < MAX_INTERESTS; i++) {
    String v = prefs.getString("interest" + String(i), "");
    if (v.length() > 0) myInterests[interestCount++] = v;
  }
  markerExists = prefs.getBool(MARKER_KEY, false);

  // Survey mode
  if (!markerExists || !digitalRead(BUTTON_SURVEY)) {
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
  }

  // ESP-NOW
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  WiFi.setChannel(espnowChannel);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_register_recv_cb(onReceive);

  // Broadcast peer
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
  // Periodic broadcast
  if (millis() - lastBroadcast > BROADCAST_INTERVAL) {
    String msg = buildMessage();
    esp_now_send((uint8_t*)"\xff\xff\xff\xff\xff\xff", (uint8_t*)msg.c_str(), msg.length());
    lastBroadcast = millis();
  }

  // Buttons
  if (!digitalRead(BUTTON_MODE)) {
    currentMode = (currentMode == MODE_SEARCH) ? MODE_CHAT : MODE_SEARCH;
    renderDisplay();
    waitRelease(BUTTON_MODE);
  }

  if (!digitalRead(BUTTON_BADGE)) {
    badgeVisible = !badgeVisible;
    renderDisplay();
    waitRelease(BUTTON_BADGE);
  }

  renderDisplay();
  delay(120);
}
