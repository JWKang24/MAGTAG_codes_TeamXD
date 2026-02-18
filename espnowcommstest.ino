#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>        // <-- add this
#include <Adafruit_NeoPixel.h>


// --------- Pins ----------
#define NEOPIXEL_PIN 21
Adafruit_NeoPixel pixels(1, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

// --------- ESP-NOW ----------
int espnowChannel = 6;               // Wi-Fi channel for ESP-NOW
unsigned long lastBroadcast = 0;
#define BROADCAST_INTERVAL 2000     // ms

// Callback for received messages
void onReceive(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  String msg = String((char*)data).substring(0, len);
  int rssi = info->rx_ctrl->rssi;

  Serial.print("Received: ");
  Serial.print(msg);
  Serial.print(" | RSSI: ");
  Serial.println(rssi);

  // Flash NeoPixel on message received
  pixels.fill(pixels.Color(0,80,80));
  pixels.show();
  delay(100);
  pixels.clear();
  pixels.show();
}

// Setup ESP-NOW
void setupESPNow() {
  WiFi.mode(WIFI_STA);
  esp_wifi_set_channel(espnowChannel, WIFI_SECOND_CHAN_NONE);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed!");
    while (true) delay(1000);
  }

  esp_now_register_recv_cb(onReceive);

  // Add broadcast peer
  uint8_t broadcastMac[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, broadcastMac, 6);
  peer.channel = espnowChannel;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  Serial.println("ESP-NOW ready!");
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  pixels.begin();
  pixels.setBrightness(50);
  pixels.clear();
  pixels.show();

  setupESPNow();
}

void loop() {
  // Periodic broadcast
  if (millis() - lastBroadcast > BROADCAST_INTERVAL) {
    String msg = "Hello from ESP32!";
    esp_now_send((uint8_t*)"\xff\xff\xff\xff\xff\xff", (uint8_t*)msg.c_str(), msg.length());
    Serial.println("Broadcast sent: " + msg);
    lastBroadcast = millis();
  }

  delay(50);
}
