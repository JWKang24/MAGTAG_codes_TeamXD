# code.py — MagTag ESP-NOW “Common Interests” (SEARCH/CHAT only)
#
# Modes:
#   - SEARCH (default)
#     - D15 (Button A): enter CHAT (auto-picks closest peer by RSSI if not pre-selected)
#     - D11 (Button D): toggle interests badge display (on/off)
#   - CHAT
#     - D15 (Button A): back to SEARCH
#     - D14 (Button B): share contact (synced)
#     - D11 (Button D): cycle to next common interest (synced on both devices)
#
# Notes:
# - Uses ESP-NOW broadcast with a fixed channel for reliability.
# - In CHAT mode, sync happens ONLY with the chosen chat peer AND only if peer targets us.
# - Common-interest index sync uses a version counter + deterministic tie-break.

import supervisor
supervisor.runtime.autoreload = False

import time
import board
import displayio
import terminalio
import neopixel
import digitalio
import espnow
import wifi
from adafruit_display_text import label


# =====================================================
# EDIT THESE per MagTag
# =====================================================
MY_NAME = "MagTag-1"
MY_INTERESTS = [
    "python",
    "music",
    "hiking",
    "gaming",
    "cooking",
    "sci-fi",
    "cats",
    "space",
]
# Optional: show a topic in SEARCH UI; not used for mode semantics
BROADCAST_TOPIC = "circuitpython"
# =====================================================

# ESP-NOW runs on a WiFi channel. All devices MUST match.
ESPNOW_CHANNEL = 6

BROADCAST_INTERVAL = 2.0       # seconds between broadcasts
PEER_TIMEOUT = 15.0            # seconds before peer considered gone
DISPLAY_REFRESH = 8.0          # minimum seconds between e-ink refreshes (avoid ghosting)
MAX_MSG_LEN = 250              # ESP-NOW payload limit

# -- Modes (ONLY TWO) --
MODE_SEARCH = 0
MODE_CHAT = 1

MODE_NAMES = ["SEARCH", "CHAT"]
MODE_DESCRIPTIONS = ["Searching for peers...", "Chatting"]
MODE_COLORS = [
    (0, 20, 0),    # SEARCH: dim green
    (20, 15, 0),   # CHAT: dim amber
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
# Channel “hack” to force channel on some CircuitPython builds
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

# Nearby peers: dict keyed by MAC bytes
# value = {name, mode, interests, topic, rssi, last_seen, peer_mac, contact_shared, common_idx, idx_ver}
nearby_peers = {}

# Chat state
chat_peer_mac = None       # MAC bytes of chosen peer
chat_common = []           # list[str] common interests
chat_common_idx = 0        # which common interest is displayed
chat_idx_ver = 0           # version counter for common_idx updates
contact_shared = False     # synced “contact shared” flag


# -- Protocol --
# Message format (ASCII, pipe-delimited, max 250 bytes):
#   MODE|NAME|interest1,interest2,...|TOPIC|CHAT_PEER_MACHEX|CONTACT_SHARED|COMMON_IDX|IDX_VER
#
# - MODE: 0 SEARCH, 1 CHAT
# - TOPIC: optional string for display in SEARCH UI (no semantics)
# - CHAT_PEER_MACHEX: in CHAT, who I am targeting (hex, no colons). empty otherwise.
# - CONTACT_SHARED: "1" if user pressed share contact in CHAT, else "0"
# - COMMON_IDX, IDX_VER: for synced cycling of common interests


def build_message():
    # Always send interests so matching works. If you want privacy, blank them in SEARCH.
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
    if len(msg) > MAX_MSG_LEN:
        msg = msg[:MAX_MSG_LEN]
    return msg


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


# -- Broadcast --
def do_broadcast():
    global last_broadcast
    msg = build_message()
    try:
        e.send(bytes(msg, "utf-8"), broadcast_peer)
    except Exception:
        pass
    last_broadcast = time.monotonic()


# -- Receive --
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

    # Drain incoming packets
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
            "peer_mac": info["peer_mac"],               # who THEY are targeting in CHAT
            "contact_shared": info["contact_shared"],
            "common_idx": info["common_idx"],
            "idx_ver": info["idx_ver"],
        }

        if old is None:
            changed = True
            flash_new_peer()
        else:
            if (old["mode"] != info["mode"] or
                old["topic"] != info["topic"] or
                old["name"] != info["name"]):
                changed = True

    # Prune stale peers
    stale = [k for k, v in nearby_peers.items() if now - v["last_seen"] > PEER_TIMEOUT]
    for k in stale:
        del nearby_peers[k]
        changed = True

    # CHAT sync: only with our chosen peer, and only if they target us
    if current_mode == MODE_CHAT and chat_peer_mac:
        peer = nearby_peers.get(chat_peer_mac)
        if peer and peer.get("peer_mac") == bytes(my_mac):
            # Recompute common list if their interests changed
            new_common, _ = compute_match(MY_INTERESTS, peer["interests"])
            if new_common != chat_common:
                chat_common = new_common
                if chat_common and chat_common_idx >= len(chat_common):
                    chat_common_idx = 0
                changed = True

            # Versioned index sync
            peer_ver = peer.get("idx_ver", 0)
            if peer_ver > chat_idx_ver:
                chat_idx_ver = peer_ver
                if chat_common:
                    chat_common_idx = peer.get("common_idx", 0) % len(chat_common)
                else:
                    chat_common_idx = 0
                changed = True
            elif peer_ver == chat_idx_ver:
                # Tie-break to converge: smaller MAC "wins"
                # If my_mac > chat_peer_mac, I adopt peer's idx.
                if bytes(my_mac) > chat_peer_mac:
                    peer_idx = peer.get("common_idx", 0)
                    if chat_common:
                        peer_idx = peer_idx % len(chat_common)
                    else:
                        peer_idx = 0
                    if peer_idx != chat_common_idx:
                        chat_common_idx = peer_idx
                        changed = True

            # Contact sharing sync: OR behavior
            if peer.get("contact_shared") and not contact_shared:
                contact_shared = True
                changed = True

    if changed:
        display_dirty = True


# -- Peer selection --
def pick_closest_peer():
    best_mac = None
    best_rssi = -999
    for mac, peer in nearby_peers.items():
        if peer["rssi"] > best_rssi:
            best_mac = mac
            best_rssi = peer["rssi"]
    return best_mac


# -- LEDs --
def update_leds(phase):
    r, g, b = MODE_COLORS[current_mode]
    if current_mode == MODE_SEARCH:
        n = len(nearby_peers)
        speed = max(20, 40 - n * 4)
        scale = abs((phase % speed) - speed // 2) / (speed / 2.0)
        br = int(g * (0.3 + 0.7 * scale))
        pixels.fill((0, br, 0))
    else:
        idx = (phase // 5) % 4
        pixels.fill((5, 4, 0))
        pixels[idx] = (min(r * 3, 255), min(g * 3, 255), 0)
        pixels[(idx + 2) % 4] = (min(r * 2, 255), min(g * 2, 255), 0)
    pixels.show()


# -- Display helpers --
def rssi_bar(rssi):
    if rssi > -50:
        return "***"
    if rssi > -70:
        return "**"
    return "*"


# -- Display --
def render_display():
    global last_display_refresh, display_dirty

    display = board.DISPLAY
    g = displayio.Group()

    # White background
    bg = displayio.Bitmap(296, 128, 1)
    pal = displayio.Palette(1)
    pal[0] = 0xFFFFFF
    g.append(displayio.TileGrid(bg, pixel_shader=pal))

    black_pal = displayio.Palette(1)
    black_pal[0] = 0x000000

    gray_pal = displayio.Palette(1)
    gray_pal[0] = 0x999999

    # Top bar divider
    bar = displayio.Bitmap(296, 3, 1)
    g.append(displayio.TileGrid(bar, pixel_shader=black_pal, x=0, y=24))

    # Mode indicator
    mode_bg = displayio.Bitmap(90, 18, 1)
    g.append(displayio.TileGrid(mode_bg, pixel_shader=black_pal, x=3, y=3))
    g.append(label.Label(
        terminalio.FONT,
        text=" " + MODE_NAMES[current_mode] + " ",
        color=0xFFFFFF,
        anchor_point=(0.0, 0.0),
        anchored_position=(6, 6),
        scale=1,
    ))

    # Name + tag
    g.append(label.Label(
        terminalio.FONT,
        text=MY_NAME + " [ESP-NOW]",
        color=0x000000,
        anchor_point=(1.0, 0.0),
        anchored_position=(290, 6),
        scale=1,
    ))

    # Status line
    g.append(label.Label(
        terminalio.FONT,
        text=MODE_DESCRIPTIONS[current_mode],
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(6, 30),
        scale=1,
    ))

    y = 42

    # SEARCH: list peers + match %
    if current_mode == MODE_SEARCH:
        if BROADCAST_TOPIC:
            g.append(label.Label(
                terminalio.FONT,
                text="Topic: " + BROADCAST_TOPIC[:30],
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=1,
            ))
            y += 12

        if nearby_peers:
            g.append(label.Label(
                terminalio.FONT,
                text="Nearby: " + str(len(nearby_peers)),
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=1,
            ))
            y += 12

            for mac_key, peer in sorted(nearby_peers.items(), key=lambda x: x[1]["rssi"], reverse=True)[:4]:
                common, pct = compute_match(MY_INTERESTS, peer["interests"])
                line = "{} {}% {}".format(peer["name"][:10], pct, rssi_bar(peer["rssi"]))
                g.append(label.Label(
                    terminalio.FONT,
                    text=line,
                    color=0x000000,
                    anchor_point=(0.0, 0.0),
                    anchored_position=(10, y),
                    scale=1,
                ))
                y += 11

        # Badge display toggle (D11)
        if badge_visible:
            sep = displayio.Bitmap(296, 1, 1)
            g.append(displayio.TileGrid(sep, pixel_shader=gray_pal, x=0, y=90))
            g.append(label.Label(
                terminalio.FONT,
                text="Interests:",
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, 94),
                scale=1,
            ))
            row1 = ", ".join(MY_INTERESTS[:4])
            row2 = ", ".join(MY_INTERESTS[4:8])
            g.append(label.Label(
                terminalio.FONT,
                text=row1,
                color=0x555555,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, 106),
                scale=1,
            ))
            if row2:
                g.append(label.Label(
                    terminalio.FONT,
                    text=row2,
                    color=0x555555,
                    anchor_point=(0.0, 0.0),
                    anchored_position=(6, 118),
                    scale=1,
                ))
        else:
            g.append(label.Label(
                terminalio.FONT,
                text="[D] show interests",
                color=0x999999,
                anchor_point=(0.5, 0.0),
                anchored_position=(148, 112),
                scale=1,
            ))

        # Hints
        g.append(label.Label(
            terminalio.FONT,
            text="A:Chat  D:Badge",
            color=0xAAAAAA,
            anchor_point=(0.5, 1.0),
            anchored_position=(148, 127),
            scale=1,
        ))

    # CHAT: show common interest + sync status
    else:
        peer_name = "(none)"
        peer_rssi = None
        if chat_peer_mac and chat_peer_mac in nearby_peers:
            peer_name = nearby_peers[chat_peer_mac]["name"][:16]
            peer_rssi = nearby_peers[chat_peer_mac]["rssi"]

        g.append(label.Label(
            terminalio.FONT,
            text="With: " + peer_name,
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, y),
            scale=1,
        ))
        y += 12

        if peer_rssi is not None:
            g.append(label.Label(
                terminalio.FONT,
                text="Signal: " + rssi_bar(peer_rssi),
                color=0x555555,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=1,
            ))
            y += 12

        if chat_common:
            common_text = "Common: " + chat_common[chat_common_idx]
            idx_text = "({}/{})".format(chat_common_idx + 1, len(chat_common))
        else:
            common_text = "Common: (none yet)"
            idx_text = ""

        g.append(label.Label(
            terminalio.FONT,
            text=common_text[:32],
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, y),
            scale=2,
        ))
        y += 22

        if idx_text:
            g.append(label.Label(
                terminalio.FONT,
                text=idx_text,
                color=0x555555,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=1,
            ))
            y += 12

        share_text = "Contact shared: YES" if contact_shared else "Contact shared: no"
        g.append(label.Label(
            terminalio.FONT,
            text=share_text,
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, 100),
            scale=1,
        ))

        g.append(label.Label(
            terminalio.FONT,
            text="A:Back  B:Share  D:Next",
            color=0xAAAAAA,
            anchor_point=(0.5, 1.0),
            anchored_position=(148, 127),
            scale=1,
        ))

    display.root_group = g
    while display.time_to_refresh > 0:
        time.sleep(0.2)
    display.refresh()

    last_display_refresh = time.monotonic()
    display_dirty = False


# -- Mode transitions --
def set_mode(new_mode):
    global current_mode, display_dirty
    global chat_peer_mac, chat_common, chat_common_idx, chat_idx_ver, contact_shared

    if new_mode == current_mode:
        return

    if new_mode == MODE_CHAT:
        # If chat not initiated by recommendation, pick closest by RSSI
        if chat_peer_mac is None:
            chat_peer_mac = pick_closest_peer()

        chat_common_idx = 0
        chat_idx_ver = 0
        contact_shared = False
        chat_common = []

        if chat_peer_mac and chat_peer_mac in nearby_peers:
            chat_common, _ = compute_match(MY_INTERESTS, nearby_peers[chat_peer_mac]["interests"])
    else:
        chat_peer_mac = None
        chat_common = []
        chat_common_idx = 0
        chat_idx_ver = 0
        contact_shared = False

    current_mode = new_mode
    pixels.fill(MODE_COLORS[new_mode])
    time.sleep(0.15)
    pixels.fill(0)
    display_dirty = True
    do_broadcast()


# ===== MAIN LOOP =====
try:
    render_display()
    do_broadcast()

    phase = 0
    while True:
        now = time.monotonic()

        # Buttons
        if current_mode == MODE_SEARCH:
            # D15: Enter chat
            if not buttons[BTN_A].value:
                set_mode(MODE_CHAT)
                wait_release(BTN_A)

            # D11: toggle badge
            elif not buttons[BTN_D].value:
                badge_visible = not badge_visible
                display_dirty = True
                wait_release(BTN_D)

        else:  # MODE_CHAT
            # D15: back to search
            if not buttons[BTN_A].value:
                set_mode(MODE_SEARCH)
                wait_release(BTN_A)

            # D14: share contact
            elif not buttons[BTN_B].value:
                contact_shared = True
                display_dirty = True
                do_broadcast()
                pixels.fill((0, 0, 80))
                time.sleep(0.15)
                pixels.fill(0)
                wait_release(BTN_B)

            # D11: next common interest (synced)
            elif not buttons[BTN_D].value:
                if chat_common:
                    chat_common_idx = (chat_common_idx + 1) % len(chat_common)
                    chat_idx_ver += 1
                    display_dirty = True
                    do_broadcast()
                pixels.fill((60, 60, 60))
                time.sleep(0.12)
                pixels.fill(0)
                wait_release(BTN_D)

        # Periodic broadcast
        if now - last_broadcast >= BROADCAST_INTERVAL:
            do_broadcast()

        # Receive
        receive_all()

        # Refresh display (rate-limited for e-ink)
        if display_dirty and (now - last_display_refresh >= DISPLAY_REFRESH):
            render_display()

        # LEDs
        update_leds(phase)
        phase = (phase + 1) % 200
        time.sleep(0.08)

except Exception as ex:
    for _ in range(10):
        pixels.fill((255, 0, 0))
        time.sleep(0.15)
        pixels.fill(0)
        time.sleep(0.15)
    try:
        display = board.DISPLAY
        g = displayio.Group()
        bg = displayio.Bitmap(296, 128, 1)
        pal = displayio.Palette(1)
        pal[0] = 0xFFFFFF
        g.append(displayio.TileGrid(bg, pixel_shader=pal))
        g.append(label.Label(
            terminalio.FONT,
            text="ERROR:\n" + str(ex)[:200],
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(4, 4),
            scale=1,
            line_spacing=1.2,
        ))
        display.root_group = g
        while display.time_to_refresh > 0:
            time.sleep(0.2)
        display.refresh()
    except Exception:
        pass