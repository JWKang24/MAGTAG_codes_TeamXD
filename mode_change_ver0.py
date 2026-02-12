# MagTag Mode Controller -- ESP-NOW Edition
# Direct peer-to-peer communication via ESP-NOW (no WiFi needed!)
#
# Button A (left)  = MUTE        -- busy, stop broadcasting
# Button B         = WAKE        -- open, broadcast + listen for others
# Button C         = HALF-WAKE   -- chatting but broadcast topic so others join
# Button D (right) = BADGE       -- toggle interest display on E-Ink
#
# ESP-NOW broadcasts your state every few seconds.
# All nearby MagTags running this code will see each other.

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
BROADCAST_TOPIC = "circuitpython"
# =====================================================

BROADCAST_INTERVAL = 3   # seconds between broadcasts
PEER_TIMEOUT = 15        # seconds before a peer is considered gone
DISPLAY_REFRESH = 10     # min seconds between e-ink refreshes

# -- Mode constants --
MODE_MUTE = 0
MODE_WAKE = 1
MODE_HALF_WAKE = 2

MODE_NAMES = ["MUTE", "WAKE", "HALF-WAKE"]
MODE_DESCRIPTIONS = [
    "Busy -- not open",
    "Open -- scanning",
    "Chatting -- join us!",
]
MODE_COLORS = [
    (20, 0, 0),    # Mute: dim red
    (0, 20, 0),    # Wake: dim green
    (20, 15, 0),   # Half-Wake: dim amber
]

# -- Hardware --
pixels = neopixel.NeoPixel(board.NEOPIXEL, 4, brightness=0.15)
pixels.fill(0)

button_pins = (board.D15, board.D14, board.D12, board.D11)
buttons = []
for pin in button_pins:
    b = digitalio.DigitalInOut(pin)
    b.direction = digitalio.Direction.INPUT
    b.pull = digitalio.Pull.UP
    buttons.append(b)

# -- ESP-NOW setup --
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'

e = espnow.ESPNow(buffer_size=2048)
broadcast_peer = espnow.Peer(mac=BROADCAST_MAC, channel=0)
e.peers.append(broadcast_peer)

my_mac = wifi.radio.mac_address
my_mac_str = ":".join("{:02X}".format(b) for b in my_mac)

# -- State --
current_mode = MODE_WAKE
badge_visible = False
last_broadcast = 0
last_display_refresh = 0
display_dirty = True

# Nearby peers: dict keyed by MAC bytes
# value = {name, mode, interests, topic, rssi, last_seen}
nearby_peers = {}


# -- Protocol --
# Message format (ASCII, pipe-delimited, max 250 bytes):
#   MODE|NAME|interest1,interest2,...|TOPIC

def build_message():
    parts = [
        str(current_mode),
        MY_NAME[:20],
        ",".join(MY_INTERESTS[:12]),
        BROADCAST_TOPIC[:30] if current_mode == MODE_HALF_WAKE else "",
    ]
    msg = "|".join(parts)
    return msg[:250]


def parse_message(data):
    try:
        text = str(data, "utf-8")
        parts = text.split("|")
        if len(parts) < 4:
            return None
        mode = int(parts[0])
        name = parts[1]
        interests = [s.strip() for s in parts[2].split(",") if s.strip()]
        topic = parts[3]
        return {"mode": mode, "name": name, "interests": interests, "topic": topic}
    except Exception:
        return None


def compute_match(mine, theirs):
    mine_set = set(s.lower() for s in mine)
    theirs_set = set(s.lower() for s in theirs)
    common = mine_set & theirs_set
    total = len(mine_set | theirs_set)
    if total == 0:
        return [], 0
    pct = int(len(common) / total * 100)
    return sorted(common), pct


# -- Broadcast --
def do_broadcast():
    global last_broadcast
    msg = build_message()
    try:
        e.send(bytes(msg, "utf-8"))
    except Exception:
        pass
    last_broadcast = time.monotonic()


# -- Receive --
def receive_all():
    global display_dirty
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
        }
        if old is None:
            changed = True
            flash_new_peer(info["name"])
        elif old["mode"] != info["mode"] or old["topic"] != info["topic"]:
            changed = True

    # Prune stale peers
    stale = [k for k, v in nearby_peers.items() if now - v["last_seen"] > PEER_TIMEOUT]
    for k in stale:
        del nearby_peers[k]
        changed = True

    if changed:
        display_dirty = True


def flash_new_peer(name):
    for _ in range(3):
        pixels.fill((0, 80, 80))
        time.sleep(0.1)
        pixels.fill(0)
        time.sleep(0.1)


# -- Color helper --
def hsv_to_rgb(h, s, v):
    if s == 0:
        return (v, v, v)
    region = h // 60
    remainder = (h - (region * 60)) * 6
    p = (v * (255 - s)) >> 8
    q = (v * (255 - ((s * remainder) >> 8))) >> 8
    t = (v * (255 - ((s * (360 - remainder)) >> 8))) >> 8
    if region == 0:
        return (v, t, p)
    elif region == 1:
        return (q, v, p)
    elif region == 2:
        return (p, v, t)
    elif region == 3:
        return (p, q, v)
    elif region == 4:
        return (t, p, v)
    return (v, p, q)


# -- LED dance scaled by match % --
def led_dance(match_pct):
    duration = 3 + (match_pct / 100.0) * 12
    delay = max(0.06 - (match_pct / 100.0) * 0.04, 0.02)
    bright = 40 + int((match_pct / 100.0) * 200)

    if match_pct == 0:
        for _ in range(6):
            for b in range(0, 40, 4):
                pixels.fill((b, 0, 0))
                time.sleep(0.05)
            for b in range(40, 0, -4):
                pixels.fill((b, 0, 0))
                time.sleep(0.05)
        pixels.fill(0)
        return

    start = time.monotonic()
    hue = 0
    while time.monotonic() - start < duration:
        for i in range(4):
            h = (hue + i * 90) % 360
            pixels[i] = hsv_to_rgb(h, 255, min(bright, 255))
        pixels.show()
        hue = (hue + 5 + match_pct // 10) % 360
        time.sleep(delay)

    sparkle_count = 5 + match_pct // 10
    for _ in range(sparkle_count):
        idx = int(time.monotonic() * 1000) % 4
        pixels.fill(0)
        pixels[idx] = (bright, bright, bright)
        pixels.show()
        time.sleep(0.08)
        pixels.fill(0)
        pixels.show()
        time.sleep(0.06)
    pixels.fill(0)


# -- NeoPixel status per mode --
def update_leds(phase):
    r, g, b = MODE_COLORS[current_mode]
    if current_mode == MODE_MUTE:
        pixels.fill(0)
        pixels[0] = (r, g, b)
    elif current_mode == MODE_WAKE:
        n = len(nearby_peers)
        if n > 0:
            speed = max(20, 40 - n * 5)
            scale = abs((phase % speed) - speed // 2) / (speed / 2.0)
        else:
            scale = abs((phase % 40) - 20) / 20.0
        br = int(g * (0.3 + 0.7 * scale))
        pixels.fill((0, br, 0))
    elif current_mode == MODE_HALF_WAKE:
        idx = (phase // 5) % 4
        pixels.fill((5, 4, 0))
        pixels[idx] = (r * 3, g * 3, 0)
        pixels[(idx + 2) % 4] = (r * 2, g * 2, 0)
    pixels.show()


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

    # Top bar
    bar = displayio.Bitmap(296, 3, 1)
    g.append(displayio.TileGrid(bar, pixel_shader=black_pal, x=0, y=24))

    # Mode indicator (inverted box)
    mode_bg = displayio.Bitmap(100, 18, 1)
    g.append(displayio.TileGrid(mode_bg, pixel_shader=black_pal, x=3, y=3))
    mode_lbl = label.Label(
        terminalio.FONT,
        text=" " + MODE_NAMES[current_mode] + " ",
        color=0xFFFFFF,
        anchor_point=(0.0, 0.0),
        anchored_position=(6, 6),
        scale=1,
    )
    g.append(mode_lbl)

    # Name + ESP-NOW tag
    name_lbl = label.Label(
        terminalio.FONT,
        text=MY_NAME + " [ESP-NOW]",
        color=0x000000,
        anchor_point=(1.0, 0.0),
        anchored_position=(290, 6),
        scale=1,
    )
    g.append(name_lbl)

    # Status line
    desc_lbl = label.Label(
        terminalio.FONT,
        text=MODE_DESCRIPTIONS[current_mode],
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(6, 30),
        scale=1,
    )
    g.append(desc_lbl)

    y_cursor = 42

    # Half-Wake: show topic
    if current_mode == MODE_HALF_WAKE and BROADCAST_TOPIC:
        t_lbl = label.Label(
            terminalio.FONT,
            text="Topic: " + BROADCAST_TOPIC,
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, y_cursor),
            scale=1,
        )
        g.append(t_lbl)
        y_cursor += 12

    # Nearby peers
    active_peers = {k: v for k, v in nearby_peers.items()
                    if v["mode"] != MODE_MUTE}
    muted_count = len(nearby_peers) - len(active_peers)

    if nearby_peers:
        count_text = "Nearby: " + str(len(active_peers)) + " open"
        if muted_count > 0:
            count_text += ", " + str(muted_count) + " busy"
        n_lbl = label.Label(
            terminalio.FONT,
            text=count_text,
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, y_cursor),
            scale=1,
        )
        g.append(n_lbl)
        y_cursor += 12

        # List peers with match %
        for mac_key, peer in sorted(active_peers.items(),
                                     key=lambda x: x[1]["name"])[:4]:
            matched, pct = compute_match(MY_INTERESTS, peer["interests"])
            mode_char = "W" if peer["mode"] == MODE_WAKE else "H"
            if peer["rssi"] > -50:
                rssi_bar = "***"
            elif peer["rssi"] > -70:
                rssi_bar = "**"
            else:
                rssi_bar = "*"
            line = peer["name"][:10] + " " + mode_char
            line += " " + str(pct) + "%" + " " + rssi_bar
            if peer["topic"]:
                line += " [" + peer["topic"][:10] + "]"
            p_lbl = label.Label(
                terminalio.FONT,
                text=line,
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(10, y_cursor),
                scale=1,
            )
            g.append(p_lbl)
            y_cursor += 11

    # Interest badges
    if badge_visible:
        sep = displayio.Bitmap(296, 1, 1)
        g.append(displayio.TileGrid(sep, pixel_shader=gray_pal, x=0, y=90))
        b_lbl = label.Label(
            terminalio.FONT,
            text="Interests:",
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, 94),
            scale=1,
        )
        g.append(b_lbl)
        row1 = ", ".join(MY_INTERESTS[:4])
        row2 = ", ".join(MY_INTERESTS[4:8])
        r1 = label.Label(
            terminalio.FONT, text=row1, color=0x555555,
            anchor_point=(0.0, 0.0), anchored_position=(6, 106), scale=1,
        )
        g.append(r1)
        if row2:
            r2 = label.Label(
                terminalio.FONT, text=row2, color=0x555555,
                anchor_point=(0.0, 0.0), anchored_position=(6, 118), scale=1,
            )
            g.append(r2)
    else:
        off_lbl = label.Label(
            terminalio.FONT,
            text="[D] to show interests",
            color=0x999999,
            anchor_point=(0.5, 0.0),
            anchored_position=(148, 112),
            scale=1,
        )
        g.append(off_lbl)

    # Button hints
    h_lbl = label.Label(
        terminalio.FONT,
        text="A:Mute B:Wake C:Half D:Badge",
        color=0xAAAAAA,
        anchor_point=(0.5, 1.0),
        anchored_position=(148, 127),
        scale=1,
    )
    g.append(h_lbl)

    display.root_group = g
    while display.time_to_refresh > 0:
        time.sleep(0.5)
    display.refresh()
    last_display_refresh = time.monotonic()
    display_dirty = False


# -- Mode transitions --
def set_mode(new_mode):
    global current_mode, display_dirty
    if new_mode == current_mode:
        return
    current_mode = new_mode
    pixels.fill(MODE_COLORS[new_mode])
    time.sleep(0.3)
    pixels.fill(0)
    time.sleep(0.1)
    display_dirty = True
    do_broadcast()


# ===== MAIN LOOP =====
try:
    render_display()
    do_broadcast()

    phase = 0

    while True:
        now = time.monotonic()

        # -- Button checks --
        if not buttons[0].value:
            set_mode(MODE_MUTE)
            while not buttons[0].value:
                time.sleep(0.05)

        elif not buttons[1].value:
            set_mode(MODE_WAKE)
            while not buttons[1].value:
                time.sleep(0.05)

        elif not buttons[2].value:
            set_mode(MODE_HALF_WAKE)
            while not buttons[2].value:
                time.sleep(0.05)

        elif not buttons[3].value:
            badge_visible = not badge_visible
            display_dirty = True
            pixels.fill((60, 60, 60))
            time.sleep(0.2)
            pixels.fill(0)
            while not buttons[3].value:
                time.sleep(0.05)

        # -- Periodic broadcast --
        if now - last_broadcast >= BROADCAST_INTERVAL:
            do_broadcast()

        # -- Receive incoming --
        receive_all()

        # -- Refresh display if needed (rate-limited for e-ink) --
        if display_dirty and (now - last_display_refresh >= DISPLAY_REFRESH):
            render_display()

        # -- LED animation --
        update_leds(phase)
        phase = (phase + 1) % 200

        time.sleep(0.08)

except Exception as ex:
    for i in range(10):
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
        err = label.Label(
            terminalio.FONT,
            text="ERROR:\n" + str(ex)[:200],
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(4, 4),
            scale=1,
            line_spacing=1.2,
        )
        g.append(err)
        display.root_group = g
        while display.time_to_refresh > 0:
            time.sleep(0.5)
        display.refresh()
    except Exception:
        pass
