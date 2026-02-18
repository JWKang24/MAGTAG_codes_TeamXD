#include <Adafruit_NeoPixel.h>

#define BUTTON_A 15
#define BUTTON_B 14
#define BUTTON_D 11
#define NEOPIXEL_PIN 21

Adafruit_NeoPixel pixels(4, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_A, INPUT_PULLUP);
  pinMode(BUTTON_B, INPUT_PULLUP);
  pinMode(BUTTON_D, INPUT_PULLUP);

  pixels.begin();
  pixels.setBrightness(50);
  pixels.clear();
  pixels.show();
  Serial.println("Button and NeoPixel test ready");
}

void loop() {
  if (!digitalRead(BUTTON_A)) {
    Serial.println("Button A pressed");
    pixels.fill(pixels.Color(255,0,0));
    pixels.show();
    delay(200);
    pixels.clear();
    pixels.show();
    while (!digitalRead(BUTTON_A)) delay(10);
  }

  if (!digitalRead(BUTTON_B)) {
    Serial.println("Button B pressed");
    pixels.fill(pixels.Color(0,255,0));
    pixels.show();
    delay(200);
    pixels.clear();
    pixels.show();
    while (!digitalRead(BUTTON_B)) delay(10);
  }

  if (!digitalRead(BUTTON_D)) {
    Serial.println("Button D pressed");
    pixels.fill(pixels.Color(0,0,255));
    pixels.show();
    delay(200);
    pixels.clear();
    pixels.show();
    while (!digitalRead(BUTTON_D)) delay(10);
  }
}
