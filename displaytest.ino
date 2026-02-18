#include <Adafruit_EPD.h>

Adafruit_SSD1680 display(296, 128, -1, -1, -1, -1, -1);

void setup() {
  display.begin();
  display.clearBuffer();
  display.setTextColor(EPD_BLACK);
  display.setCursor(10,10);
  display.print("MagTag Display Test");
  display.display();
}

void loop() {
  // Optional: periodic updates
}
