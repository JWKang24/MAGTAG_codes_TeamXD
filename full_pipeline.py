import time
import wifi

# Stage 1: run survey flow. This module now exits after Save.
import user_survey

print("Survey complete. Switching to ESP-NOW matching mode...")
time.sleep(0.5)

try:
    if hasattr(user_survey, "server") and hasattr(user_survey.server, "stop"):
        user_survey.server.stop()
except Exception:
    pass

try:
    wifi.radio.stop_station()
except Exception:
    pass

# Stage 2: start nearby user search/chat logic.
import rssi_espnow
