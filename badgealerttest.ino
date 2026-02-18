#include <Adafruit_NeoPixel.h>
#define NEOPIXEL_PIN 21
Adafruit_NeoPixel pixels(4, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

int testRSSI = -60;
int RSSI_THRESHOLD = -65;

void flashBadge() {
  for(int i=0;i<2;i++){
    pixels.fill(pixels.Color(0,80,80));
    pixels.show();
    delay(80);
    pixels.clear();
    pixels.show();
    delay(80);
  }
}

void setup() {
  pixels.begin();
  pixels.setBrightness(50);
  Serial.begin(115200);
  if(testRSSI > RSSI_THRESHOLD){
    Serial.println("Badge Alert Triggered!");
    flashBadge();
  }
}

void loop(){}
