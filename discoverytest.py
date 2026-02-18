import wifi
import espnow
import time

wifi.radio.enabled = False
wifi.radio.enabled = True
wifi.radio.stop_ap()
wifi.radio.start_station()
wifi.radio.set_channel(1)

e = espnow.ESPNow()
e.active(True)

BROADCAST = b"\xff\xff\xff\xff\xff\xff"

print("MAC:", wifi.radio.mac_address)

while True:
    e.send(BROADCAST, b"hello", sync=False)

    host, msg = e.recv(0.2)
    if msg:
        print("RX from", host, msg)

    time.sleep(1)
