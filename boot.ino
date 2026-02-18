#include <Preferences.h>
#include <WiFi.h>

#define BUTTON_A 15  // MagTag Button A

Preferences prefs;

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_A, INPUT_PULLUP);
  delay(400);  // give USB time to enumerate

  bool buttonHeld = digitalRead(BUTTON_A) == LOW;
  bool serialConnected = Serial;

  prefs.begin("boot", false);
  bool startEspNow = prefs.getBool("start_espnow", false);

  if (buttonHeld || serialConnected) {
    Serial.println("Maintenance override → survey mode");
    prefs.putBool("start_espnow", false);
    startEspNow = false;
  }

  if (startEspNow) {
    Serial.println("Phase 2 → ESP-NOW runtime");
    prefs.putBool("start_espnow", false);  // clear marker
  } else {
    Serial.println("Phase 1 → user survey");
  }

  prefs.end();
  delay(200);
  ESP.restart();  // reboot into the selected sketch/partition
}

void loop() {}
