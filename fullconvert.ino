#include <WiFi.h>                 // For Wi-Fi connectivity (STA/AP mode)
#include <esp_now.h>              // For ESP-NOW communication
#include <Preferences.h>          // For NVS storage of survey and settings
#include <Adafruit_EPD.h>         // For E-Ink display handling
#include <Adafruit_NeoPixel.h>    // For NeoPixel LED control
#include <WebServer.h>            // For serving the user survey page

// --------- Pins ----------
#define BUTTON_A 15               // Button A (enter chat / toggle modes)
#define BUTTON_B 14               // Button B (share / pairing)
#define BUTTON_D 11               // Button D (toggle badge display)
#define NEOPIXEL_PIN 21           // NeoPixel data pin

// --------- Constants ----------
#define MAX_PEERS 32              // Max number of nearby devices tracked
#define MAX_INTERESTS 12          // Max number of user interests
#define MAX_MSG_LEN 250           // Max length of ESP-NOW message
#define BROADCAST_INTERVAL 2000   // Time between broadcasts (ms)
#define PEER_TIMEOUT 15000        // Peer timeout to remove stale peers (ms)
#define RSSI_BADGE_THRESHOLD -65  // Minimum RSSI for badge alert
#define SURVEY_PORT 80            // HTTP server port for survey

// MagTag 2.9" b/w display pins
#define EPD_CS   10
#define EPD_DC   9
#define EPD_RST  6
#define EPD_BUSY 5

Adafruit_SSD1680 display(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY);

// --------- LEDs ----------
Adafruit_NeoPixel pixels(4, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800); // 4 NeoPixels

// --------- State ----------
Preferences prefs;             // NVS storage object
bool surveyComplete = false;    // Flag if survey has been completed
bool badgeVisible = false;      // Flag for showing badge interests
String myName = "MagTag";       // Default device/user name
String myInterests[MAX_INTERESTS]; // Array to store user interests
int interestCount = 0;          // Current number of interests
int espnowChannel = 6;          // ESP-NOW Wi-Fi channel

// --------- Modes ----------
enum Mode { MODE_SEARCH = 0, MODE_CHAT = 1 }; // Device operating modes
Mode currentMode = MODE_SEARCH;               // Current mode

// --------- Peer Struct ----------
struct PeerInfo {
  uint8_t mac[6];              // Peer MAC address
  String name;                 // Peer name
  String interests[MAX_INTERESTS]; // Peer interests
  int interestCount;           // Number of interests
  int rssi;                    // Last RSSI reading
  unsigned long lastSeen;      // Last seen timestamp
};
PeerInfo peers[MAX_PEERS];      // Array to hold nearby peers
int peerCount = 0;              // Current number of nearby peers

// --------- Web server ----------
WebServer server(SURVEY_PORT);  // HTTP server for survey page

// --------- Marker for boot ----------
#define MARKER_FILE "/start_espnow" // Marker to indicate survey completion
bool markerExists = false;         // True if marker exists

// --------- Helpers ----------

// Build the ESP-NOW broadcast message
String buildMessage() {
  String interests = "";
  for (int i = 0; i < interestCount; i++) {
    interests += myInterests[i];        // Add each interest
    if (i < interestCount - 1) interests += ","; // Comma-separated
  }

  // Mode|Name|Interests||||0|0
  String msg = String((int)currentMode) + "|" +
               myName.substring(0,20) + "|" +
               interests + "||||0|0";

  return msg.substring(0, MAX_MSG_LEN); // Ensure max length
}

// Flash NeoPixels to indicate a badge match
void flashBadgeMatch() {
  for (int i = 0; i < 2; i++) {
    pixels.fill(pixels.Color(0,80,80)); // Cyan flash
    pixels.show();
    delay(80);
    pixels.clear();
    pixels.show();
    delay(80);
  }
}

// Wait for button release to avoid repeated triggers
void waitRelease(int pin) {
  while (!digitalRead(pin)) delay(30);
}

// --------- ESP-NOW RX callback ----------
void onReceive(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  String msg = String((char*)data).substring(0, len); // Convert to string
  int rssi = info->rx_ctrl->rssi;                     // Get RSSI

  bool found = false;
  // Check if peer already tracked
  for (int i = 0; i < peerCount; i++) {
    if (!memcmp(peers[i].mac, info->src_addr, 6)) {  // Compare MAC
      peers[i].rssi = rssi;
      peers[i].lastSeen = millis();                  // Update timestamp
      found = true;
      break;
    }
  }

  // If new peer and space available
  if (!found && peerCount < MAX_PEERS) {
    memcpy(peers[peerCount].mac, info->src_addr, 6);
    peers[peerCount].rssi = rssi;
    peers[peerCount].lastSeen = millis();
    peerCount++;

    // Flash if RSSI strong enough
    if (rssi > RSSI_BADGE_THRESHOLD) {
      flashBadgeMatch();
    }
  }
}

// --------- Display ----------
void renderDisplay() {
  display.begin();          // Initialize display
  display.clearBuffer();    // Clear buffer
  display.setTextColor(EPD_BLACK);
  display.setCursor(4, 16);
  display.print(currentMode == MODE_SEARCH ? "SEARCH" : "CHAT"); // Show mode
  display.setCursor(4, 36);
  display.print("Nearby: "); 
  display.print(peerCount);  // Show number of peers
  display.display();         // Update E-Ink
}

// --------- Survey Page ----------
String buildSurveyPage() {
  String html = "<html><body>";
  html += "<h2>Badge Setup</h2>";
  html += "<form method='POST'>";
  html += "Name: <input name='name' value='" + myName + "'><br>";
  // Add input fields for interests
  for (int i = 0; i < MAX_INTERESTS; i++) {
    html += "<input type='text' name='interest" + String(i) + "' value='";
    if (i < interestCount) html += myInterests[i];
    html += "'><br>";
  }
  html += "<input type='submit' value='Save'>";
  html += "</form></body></html>";
  return html;
}

// Handle survey HTTP requests
void handleSurvey() {
  if (server.method() == HTTP_POST) {
    myName = server.arg("name"); // Save name
    interestCount = 0;
    // Save all entered interests
    for (int i = 0; i < MAX_INTERESTS; i++) {
      String argName = "interest" + String(i);
      String val = server.arg(argName);
      if (val.length() > 0) {
        myInterests[interestCount++] = val;
      }
    }
    // Save preferences to NVS
    prefs.begin("cfg", false);
    prefs.putString("name", myName);
    for (int i = 0; i < interestCount; i++) {
      prefs.putString("interest" + String(i), myInterests[i]);
    }
    prefs.end();

    surveyComplete = true;
    markerExists = true; // Set marker for boot skipping
  }
  server.send(200, "text/html", buildSurveyPage()); // Serve HTML page
}

// --------- Setup ----------
void setup() {
  Serial.begin(115200);
  delay(1000);

  // Setup buttons
  pinMode(BUTTON_A, INPUT_PULLUP);
  pinMode(BUTTON_B, INPUT_PULLUP);
  pinMode(BUTTON_D, INPUT_PULLUP);

  // Setup NeoPixels
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

  // Check if survey already done
  markerExists = prefs.getBool("marker", false);

  if (!markerExists) {
    // --------- RUN SURVEY ----------
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    WiFi.softAP("MagTag Survey"); // Start AP for survey

    server.on("/", handleSurvey);
    server.begin();

    renderDisplay();
    while (!surveyComplete) {      // Wait until survey completed
      server.handleClient();       // Handle HTTP requests
      renderDisplay();
      delay(50);
    }

    // Save marker
    prefs.begin("cfg", false);
    prefs.putBool("marker", true);
    prefs.end();
  }

  // --------- RUN ESP-NOW ----------
  WiFi.mode(WIFI_STA);
  esp_wifi_set_channel(espnowChannel, WIFI_SECOND_CHAN_NONE); // Set Wi-Fi channel

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_register_recv_cb(onReceive); // Register receive callback

  // Setup broadcast peer
  uint8_t broadcastMac[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, broadcastMac, 6);
  peer.channel = espnowChannel;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  renderDisplay(); // Initial display render
}

// --------- Loop ----------
unsigned long lastBroadcast = 0;
void loop() {
  // Periodic broadcast
  if (millis() - lastBroadcast > BROADCAST_INTERVAL) {
    String msg = buildMessage(); // Build message
    esp_now_send((uint8_t*)"\xff\xff\xff\xff\xff\xff", (uint8_t*)msg.c_str(), msg.length()); // Broadcast
    lastBroadcast = millis();
  }

  // Button A: toggle search/chat mode
  if (!digitalRead(BUTTON_A)) {
    currentMode = (currentMode == MODE_SEARCH) ? MODE_CHAT : MODE_SEARCH;
    renderDisplay();
    waitRelease(BUTTON_A);
  }

  // Button D: toggle badge display
  if (!digitalRead(BUTTON_D)) {
    badgeVisible = !badgeVisible;
    renderDisplay();
    waitRelease(BUTTON_D);
  }

  renderDisplay(); // Refresh display
  delay(120);
}
