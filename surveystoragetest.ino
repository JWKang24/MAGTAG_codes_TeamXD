#include <Preferences.h>

Preferences prefs;
String myName;
String myInterests[12];
int interestCount = 0;

void setup() {
  Serial.begin(115200);
  prefs.begin("cfg", true);
  
  myName = prefs.getString("name", "MagTag");
  interestCount = 0;
  for (int i=0;i<12;i++){
    String v = prefs.getString("interest" + String(i), "");
    if(v.length() > 0) myInterests[interestCount++] = v;
  }

  Serial.println("Loaded preferences:");
  Serial.println("Name: " + myName);
  for(int i=0;i<interestCount;i++) Serial.println("Interest: " + myInterests[i]);
}

void loop() {
  // For testing, allow new input from serial
  if(Serial.available()) {
    String input = Serial.readStringUntil('\n');
    myName = input;
    prefs.putString("name", myName);
    Serial.println("Name updated and saved: " + myName);
  }
  delay(500);
}
