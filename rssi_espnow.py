# code.py — MagTag ESP-NOW “Common Interests” (SEARCH/CHAT) + badge match alerts

import supervisor
supervisor.runtime.autoreload = False

import time
import os
import board
import displayio
import terminalio
import neopixel
import digitalio
import espnow
import wifi
from adafruit_display_text import label

# ---------------------------
# Load settings.toml config
# ---------------------------
def _get_env_str(key, default=""):
    v = os.getenv(key)
    if v is None:
        return default
    return str(v)

def _get_env_int(key, default):
    v = os.getenv(key)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default

def _parse_interests(csv_text):
    if not csv_text:
        return []
    parts = [p.strip() for p in csv_text.split(",")]
    return [p for p in parts if p][:12]

MY_NAME = _get_env_str("MY_NAME", "MagTag")
BROADCAST_TOPIC = _get_env_str("BROADCAST_TOPIC", "circuitpython")
MY_INTERESTS = _parse_interests(_get_env_str("MY_INTERESTS", "python,circuitpython"))
ESPNOW_CHANNEL = _get_env_int("ESPNOW_CHANNEL", 6)

# Timing
BROADCAST_INTERVAL = 2.0
PEER_TIMEOUT = 15.0
DISPLAY_REFRESH = 8.0
MAX_MSG_LEN = 250

# -- Modes --
MODE_SEARCH = 0
MODE_CHAT = 1

MODE_NAMES = ["SEARCH", "CHAT"]
MODE_DESCRIPTIONS = ["Searching for peers...", "Chatting"]
MODE_COLORS = [
    (0, 20, 0),    # SEARCH
    (20, 15, 0),   # CHAT
]

# -- Hardware --
pixels = neopixel.NeoPixel(board.NEOPIXEL, 4, brightness=0.15)
pixels.fill(0)

# MagTag buttons: A,B,C,D = D15,D14,D12,D11
button_pins = (board.D15, board.D14, board.D12, board.D11)
buttons = []
for pin in button_pins:
    b = digitalio.DigitalInOut(pin)
    b.direction = digitalio.Direction.INPUT
    b.pull = digitalio.Pull.UP
    buttons.append(b)

BTN_A, BTN_B, BTN_C, BTN_D = 0, 1, 2, 3

def wait_release(btn_index):
    while not buttons[btn_index].value:
        time.sleep(0.03)

# -- ESP-NOW setup --
wifi.radio.enabled = True
wifi.radio.start_ap(" ", "", channel=ESPNOW_CHANNEL, max_connections=0)
wifi.radio.stop_ap()

BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"
e = espnow.ESPNow(buffer_size=1024)
broadcast_peer = espnow.Peer(mac=BROADCAST_MAC, channel=ESPNOW_CHANNEL)
e.peers.append(broadcast_peer)

my_mac = wifi.radio.mac_address

# -- State --
current_mode = MODE_SEARCH
badge_visible = False
last_broadcast = 0.0
last_display_refresh = 0.0
display_dirty = True

# Nearby peers
nearby_peers = {}

# Chat state
chat_peer_mac = None
chat_common = []
chat_common_idx = 0
chat_idx_ver = 0
contact_shared = False

# -- Badge match alert state --
RSSI_BADGE_THRESHOLD = -65
seen_badge_devices = set()

# -------------------------
# Helper functions
# -------------------------
def build_message():
    interests_str = ",".join(MY_INTERESTS[:12])
    topic_str = BROADCAST_TOPIC[:30] if current_mode == MODE_SEARCH else ""
    peer_mac_hex = ""
    shared_flag = "0"
    idx_str = "0"
    ver_str = "0"

    if current_mode == MODE_CHAT:
        peer_mac_hex = chat_peer_mac.hex() if chat_peer_mac else ""
        shared_flag = "1" if contact_shared else "0"
        idx_str = str(chat_common_idx)
        ver_str = str(chat_idx_ver)

    parts = [
        str(current_mode),
        MY_NAME[:20],
        interests_str,
        topic_str,
        peer_mac_hex,
        shared_flag,
        idx_str,
        ver_str,
    ]
    msg = "|".join(parts)
    return msg[:MAX_MSG_LEN]

def parse_message(data):
    try:
        text = str(data, "utf-8")
        parts = text.split("|")
        while len(parts) < 8:
            parts.append("")
        mode = int(parts[0])
        name = parts[1]
        interests = [s.strip() for s in parts[2].split(",") if s.strip()]
        topic = parts[3]
        peer_mac = bytes.fromhex(parts[4]) if parts[4] else None
        shared = (parts[5] == "1")
        common_idx = int(parts[6]) if parts[6] else 0
        idx_ver = int(parts[7]) if parts[7] else 0
        return {
            "mode": mode,
            "name": name,
            "interests": interests,
            "topic": topic,
            "peer_mac": peer_mac,
            "contact_shared": shared,
            "common_idx": common_idx,
            "idx_ver": idx_ver,
        }
    except Exception:
        return None

def compute_match(mine, theirs):
    mine_set = set(s.lower() for s in mine)
    theirs_set = set(s.lower() for s in theirs)
    common = mine_set & theirs_set
    total = len(mine_set | theirs_set)
    if total == 0:
        return [], 0
    pct = int((len(common) / total) * 100)
    return sorted(common), pct

# -------------------------
# Badge match alert
# -------------------------
def check_badge_matches(packet_mac, peer_info):
    global seen_badge_devices
    if packet_mac == bytes(my_mac):
        return
    if packet_mac in seen_badge_devices:
        return
    rssi = peer_info.get("rssi", -100)
    if rssi < RSSI_BADGE_THRESHOLD:
        return
    peer_interests = peer_info.get("interests", [])
    shared = set(MY_INTERESTS) & set(peer_interests)
    if shared:
        print("ALERT! Shared badges with {}: {}".format(peer_info.get("name", ""), list(shared)))
        for _ in range(2):
            pixels.fill((0, 80, 80))
            time.sleep(0.08)
            pixels.fill(0)
            time.sleep(0.08)
        seen_badge_devices.add(packet_mac)

# -------------------------
# Broadcast / receive
# -------------------------
def do_broadcast():
    global last_broadcast
    msg = build_message()
    try:
        e.send(bytes(msg, "utf-8"), broadcast_peer)
    except Exception:
        pass
    last_broadcast = time.monotonic()

def flash_new_peer():
    for _ in range(2):
        pixels.fill((0, 80, 80))
        time.sleep(0.08)
        pixels.fill(0)
        time.sleep(0.08)

def receive_all():
    global display_dirty, chat_common, chat_common_idx, contact_shared, chat_idx_ver

    changed = False
    now = time.monotonic()

    while e:
        packet = e.read()
        if packet is None:
            break

        info = parse_message(packet.msg)
        if info is None:
            continue

        mac_key = bytes(packet.mac)
        if mac_key == bytes(my_mac):
            continue

        old = nearby_peers.get(mac_key)
        nearby_peers[mac_key] = {
            "name": info["name"],
            "mode": info["mode"],
            "interests": info["interests"],
            "topic": info["topic"],
            "rssi": packet.rssi,
            "last_seen": now,
            "peer_mac": info["peer_mac"],
            "contact_shared": info["contact_shared"],
            "common_idx": info["common_idx"],
            "idx_ver": info["idx_ver"],
        }

        # --- badge match alert ---
        check_badge_matches(mac_key, nearby_peers[mac_key])

        if old is None:
            changed = True
            flash_new_peer()
        else:
            if (old["mode"] != info["mode"] or
                old["topic"] != info["topic"] or
                old["name"] != info["name"]):
                changed = True

    # prune stale
    stale = [k for k, v in nearby_peers.items() if now - v["last_seen"] > PEER_TIMEOUT]
    for k in stale:
        del nearby_peers[k]
        changed = True

    # CHAT sync logic remains unchanged
    if current_mode == MODE_CHAT and chat_peer_mac:
        peer = nearby_peers.get(chat_peer_mac)
        if peer and peer.get("peer_mac") == bytes(my_mac):
            new_common, _ = compute_match(MY_INTERESTS, peer["interests"])
            if new_common != chat_common:
                chat_common = new_common
                if chat_common and chat_common_idx >= len(chat_common):
                    chat_common_idx = 0
                changed = True

            peer_ver = peer.get("idx_ver", 0)
            if peer_ver > chat_idx_ver:
                chat_idx_ver = peer_ver
                chat_common_idx = (peer.get("common_idx", 0) % len(chat_common)) if chat_common else 0
                changed = True
            elif peer_ver == chat_idx_ver:
                if bytes(my_mac) > chat_peer_mac:
                    peer_idx = peer.get("common_idx", 0)
                    peer_idx = (peer_idx % len(chat_common)) if chat_common else 0
                    if peer_idx != chat_common_idx:
                        chat_common_idx = peer_idx
                        changed = True

            if peer.get("contact_shared") and not contact_shared:
                contact_shared = True
                changed = True

    if changed:
        display_dirty = True

# -------------------------
# Pick closest peer
# -------------------------
def pick_closest_peer():
    best_mac = None
    best_rssi = -999
    for mac, peer in nearby_peers.items():
        if peer["rssi"] > best_rssi:
            best_mac = mac
            best_rssi = peer["rssi"]
    return best_mac

# -------------------------
# Display / LEDs / Mode transitions
# -------------------------
# ... (all display, LED, render_display(), set_mode() remain identical to original)
# Use exactly your previous code for display, LED, and mode transitions.
# Only modification: receive_all() now calls check_badge_matches()
