#include <Adafruit_NeoPixel.h>

// Board-defined NeoPixel pin and power control
#define PIXEL_PIN      PIN_NEOPIXEL
#define PIXEL_POWER    NEOPIXEL_POWER

#define BUTTON_A 15
#define BUTTON_B 14
#define BUTTON_D 11

Adafruit_NeoPixel pixels(4, PIXEL_PIN, NEO_GRB + NEO_KHZ800);

void setup() {
  Serial.begin(115200);

  pinMode(BUTTON_A, INPUT_PULLUP);
  pinMode(BUTTON_B, INPUT_PULLUP);
  pinMode(BUTTON_D, INPUT_PULLUP);

  // Turn on NeoPixel power
  pinMode(PIXEL_POWER, OUTPUT);
  digitalWrite(PIXEL_POWER, LOW); // LOW = ON for MagTag
  delay(10);

  pixels.begin();
  pixels.setBrightness(50);
  pixels.clear();
  pixels.show();

  Serial.println("MagTag NeoPixels ready");
}

void showColor(uint32_t c) {
  digitalWrite(PIXEL_POWER, LOW); // ensure power ON
  pixels.fill(c);
  pixels.show();
}

void loop() {
  if (!digitalRead(BUTTON_A)) {
    Serial.println("Button A");
    showColor(pixels.Color(255,0,0));
    delay(200);
    showColor(0);
    while (!digitalRead(BUTTON_A)) delay(10);
  }

  if (!digitalRead(BUTTON_B)) {
    Serial.println("Button B");
    showColor(pixels.Color(0,255,0));
    delay(200);
    showColor(0);
    while (!digitalRead(BUTTON_B)) delay(10);
  }

  if (!digitalRead(BUTTON_D)) {
    Serial.println("Button D");
    showColor(pixels.Color(0,0,255));
    delay(200);
    showColor(0);
    while (!digitalRead(BUTTON_D)) delay(10);
  }
}
