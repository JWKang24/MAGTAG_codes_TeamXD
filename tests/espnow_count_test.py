import time
import os
import wifi
import espnow


def _get_env_int(key, default):
    v = os.getenv(key)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default


ESPNOW_CHANNEL = _get_env_int("ESPNOW_CHANNEL", 6)
BROADCAST_INTERVAL = 1.0
PEER_TIMEOUT = 8.0
PRINT_INTERVAL = 1.0


# Put radio on a fixed channel for ESP-NOW.
wifi.radio.enabled = True
wifi.radio.start_ap(" ", "", channel=ESPNOW_CHANNEL, max_connections=0)
wifi.radio.stop_ap()

e = espnow.ESPNow(buffer_size=512)
broadcast_peer = espnow.Peer(mac=b"\xff\xff\xff\xff\xff\xff", channel=ESPNOW_CHANNEL)
e.peers.append(broadcast_peer)

my_mac = bytes(wifi.radio.mac_address)
nearby = {}  # mac(bytes) -> {"last_seen": t, "rssi": rssi}

last_broadcast = 0.0
last_print = 0.0
seq = 0

print("ESP-NOW count test started on channel", ESPNOW_CHANNEL)
print("My MAC:", my_mac.hex())

while True:
    now = time.monotonic()

    # Broadcast tiny heartbeat.
    if now - last_broadcast >= BROADCAST_INTERVAL:
        msg = "PING|{}".format(seq)
        try:
            e.send(bytes(msg, "utf-8"), broadcast_peer)
        except Exception:
            pass
        last_broadcast = now
        seq = (seq + 1) % 100000

    # Drain receive buffer.
    while e:
        packet = e.read()
        if packet is None:
            break

        mac = bytes(packet.mac)
        if mac == my_mac:
            continue

        nearby[mac] = {"last_seen": now, "rssi": packet.rssi}

    # Prune stale peers.
    stale = [m for m, info in nearby.items() if now - info["last_seen"] > PEER_TIMEOUT]
    for m in stale:
        del nearby[m]

    # Print nearby count only.
    if now - last_print >= PRINT_INTERVAL:
        print("Nearby ESP-NOW devices:", len(nearby))
        last_print = now

    time.sleep(0.05)
