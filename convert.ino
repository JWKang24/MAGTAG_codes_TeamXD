#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <Adafruit_EPD.h>
#include "qrcode.h"
#include <esp_now.h>

#define BUTTON_A 15
#define MAX_INTERESTS 5

// --------- Globals ----------
Preferences prefs;
AsyncWebServer server(80);

// MagTag 2.9" e-ink (SSD1680)
Adafruit_SSD1680 display(296, 128, -1, -1, -1, -1, -1);

bool surveyMode = false;
bool surveyComplete = false;

String myName = "MagTag";
String interests[MAX_INTERESTS];
int interestCount = 0;

const char* SSID = "YOUR_WIFI";
const char* PASS = "YOUR_PASS";

const char* ALL_INTERESTS[] = {"python", "circuitpython", "electronics"};
const int INTEREST_COUNT = 3;

// --------- HTML ----------
String page(const String& msg) {
  String html =
    "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>Badge Setup</title></head><body style='font-family:sans-serif;max-width:560px;margin:20px auto;'>"
    "<h2>Badge Setup</h2>";

  if (msg.length()) html += "<p style='color:#0a7a2f'><b>" + msg + "</b></p>";

  html += "<form method='POST'>"
          "<label><b>Name</b></label><br>"
          "<input name='name' value='" + myName + "' style='width:100%;padding:8px;'><br><br>"
          "<b>Choose up to 5 interests</b><br>";

  for (int i = 0; i < INTEREST_COUNT; i++) {
    html += "<label><input type='checkbox' name='badge' value='" + String(ALL_INTERESTS[i]) + "'> ";
    html += ALL_INTERESTS[i];
    html += "</label><br>";
  }

  html += "<br><input type='submit' value='Save'></form></body></html>";
  return html;
}

// --------- QR ----------
void drawQR(const char* url) {
  display.begin();
  display.clearBuffer();

  QRCode qr;
  uint8_t data[qrcode_getBufferSize(3)];
  qrcode_initText(&qr, data, 3, ECC_LOW, url);

  int scale = 2;
  int x0 = (296 - qr.size * scale) / 2;
  int y0 = (128 - qr.size * scale) / 2;

  for (int y = 0; y < qr.size; y++) {
    for (int x = 0; x < qr.size; x++) {
      if (qrcode_getModule(&qr, x, y)) {
        display.fillRect(x0 + x * scale, y0 + y * scale, scale, scale, EPD_BLACK);
      }
    }
  }
  display.display();
}

// --------- Survey ----------
void startSurvey() {
  Serial.println("Starting survey mode");

  prefs.begin("cfg", false);
  myName = prefs.getString("name", "MagTag");

  WiFi.begin(SSID, PASS);
  while (WiFi.status() != WL_CONNECTED) delay(300);

  String url = "http://" + WiFi.localIP().toString();
  drawQR(url.c_str());

  server.on("/", HTTP_GET, [](AsyncWebServerRequest* req) {
    req->send(200, "text/html", page(""));
  });

  server.on("/", HTTP_POST, [](AsyncWebServerRequest* req) {
    if (req->hasParam("name", true)) {
      myName = req->getParam("name", true)->value();
      prefs.putString("name", myName);
    }

    interestCount = 0;
    for (int i = 0; i < req->params(); i++) {
      auto* p = req->getParam(i);
      if (p->isPost() && p->name() == "badge" && interestCount < MAX_INTERESTS) {
        interests[interestCount++] = p->value();
      }
    }

    prefs.putBool("start_espnow", true);  // marker
    surveyComplete = true;

    req->send(200, "text/html", page("Saved. Rebooting into ESP-NOW mode..."));
  });

  server.begin();
}

// --------- ESP-NOW ----------
void startEspNow() {
  Serial.println("Starting ESP-NOW runtime");

  prefs.begin("cfg", true);
  myName = prefs.getString("name", "MagTag");
  prefs.end();

  WiFi.mode(WIFI_STA);
  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  Serial.println("ESP-NOW ready");
}

// --------- Setup ----------
void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_A, INPUT_PULLUP);
  delay(400);

  bool buttonHeld = digitalRead(BUTTON_A) == LOW;
  bool serialConnected = Serial;

  prefs.begin("boot", false);
  bool startEspNow = prefs.getBool("start_espnow", false);

  if (buttonHeld || serialConnected) {
    Serial.println("Maintenance override → survey");
    prefs.putBool("start_espnow", false);
    startEspNow = false;
  }

  prefs.end();

  if (startEspNow) {
    surveyMode = false;
    startEspNow();
  } else {
    surveyMode = true;
    startSurvey();
  }
}

// --------- Loop ----------
void loop() {
  if (surveyMode && surveyComplete) {
    delay(500);
    ESP.restart();   // clean jump to ESP-NOW phase
  }

  // ESP-NOW main loop lives here
}
