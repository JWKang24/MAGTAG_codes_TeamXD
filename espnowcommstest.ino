#include <WiFi.h>
#include <esp_now.h>

uint8_t broadcastMac[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
unsigned long lastBroadcast = 0;
#define BROADCAST_INTERVAL 2000

void onReceive(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  String msg = String((char*)data).substring(0,len);
  int rssi = info->rx_ctrl->rssi;
  Serial.print("Received: "); Serial.print(msg);
  Serial.print(" | RSSI: "); Serial.println(rssi);
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  esp_wifi_set_channel(6, WIFI_SECOND_CHAN_NONE);

  if(esp_now_init() != ESP_OK) Serial.println("ESP-NOW init failed");
  esp_now_register_recv_cb(onReceive);

  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, broadcastMac, 6);
  peer.channel = 6;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  Serial.println("ESP-NOW setup complete");
}

void loop() {
  if(millis() - lastBroadcast > BROADCAST_INTERVAL){
    String msg = "Hello MagTag";
    esp_now_send(broadcastMac, (uint8_t*)msg.c_str(), msg.length());
    Serial.println("Broadcasted: " + msg);
    lastBroadcast = millis();
  }
}
