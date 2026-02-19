# import supervisor
# supervisor.runtime.autoreload = False

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
chat_force_empty_topic = False

# Search-mode match LED latch state
search_match_latched = False
search_match_topic = ""
search_match_color = (0, 0, 0)

# -- Badge match alert state --
RSSI_BADGE_THRESHOLD = -65
seen_badge_devices = set()

# -------------------------
# Helper functions
# -------------------------
def build_message():
    interests_str = ",".join(MY_INTERESTS[:12])
    topic_str = ""
    peer_mac_hex = ""
    shared_flag = "0"
    idx_str = "0"
    ver_str = "0"

    if current_mode == MODE_CHAT:
        if (not chat_force_empty_topic) and chat_common:
            topic_str = chat_common[chat_common_idx][:30]
        if isinstance(chat_peer_mac, (bytes, bytearray)):
            peer_mac_hex = chat_peer_mac.hex()
        else:
            peer_mac_hex = ""
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
        topic = parts[3].strip()
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


def first_common_interest(mine, theirs):
    theirs_set = set(s.lower() for s in theirs)
    for item in mine:
        if item.lower() in theirs_set:
            return item
    return None


def find_best_shared_match():
    """Return (topic, name, rssi) for best nearby shared-interest peer."""
    best_topic = None
    best_name = ""
    best_rssi = -999
    for _, peer in nearby_peers.items():
        topic = ""
        if peer.get("mode") == MODE_CHAT and peer.get("topic"):
            peer_topic = peer.get("topic", "")
            if any(peer_topic.lower() == mine.lower() for mine in MY_INTERESTS):
                topic = peer_topic
        if not topic:
            topic = first_common_interest(MY_INTERESTS, peer.get("interests", []))
        rssi = peer.get("rssi", -999)
        if topic and rssi > best_rssi:
            best_topic = topic
            best_name = peer.get("name", "")
            best_rssi = rssi
    return best_topic, best_name, best_rssi


def interest_to_led_color(topic):
    """
    Deterministically map a shared-interest topic to a visible LED color.
    Same topic -> same color across devices.
    """
    if not topic:
        return (0, 80, 80)
    h = 0
    for ch in topic.lower():
        h = ((h * 33) + ord(ch)) & 0xFFFF
    palette = (
        (120, 30, 30),
        (30, 120, 30),
        (30, 30, 120),
        (120, 90, 20),
        (20, 120, 90),
        (90, 20, 120),
        (120, 50, 90),
        (60, 120, 40),
    )
    return palette[h % len(palette)]


def _safe_topic_chars(text):
    """CircuitPython-friendly sanitizer without str.isalnum()."""
    out = ""
    for ch in text:
        if ch in ("_", "-"):
            out += ch
            continue
        code = ord(ch)
        is_digit = 48 <= code <= 57
        is_upper = 65 <= code <= 90
        is_lower = 97 <= code <= 122
        if is_digit or is_upper or is_lower:
            out += ch
    return out


def _topic_to_image_path(topic):
    """Map a topic string to a BMP in /images, returning None if not found."""
    if not topic:
        return None

    raw = topic.strip()
    if not raw:
        return None

    names = []
    variants = (
        raw,
        raw.lower(),
        raw.replace(" ", "_"),
        raw.lower().replace(" ", "_"),
        raw.replace(" ", "-"),
        raw.lower().replace(" ", "-"),
    )
    for item in variants:
        safe = _safe_topic_chars(item)
        if safe and safe not in names:
            names.append(safe)

    for name in names:
        p = "/images/{}.bmp".format(name)
        try:
            os.stat(p)
            return p
        except OSError:
            pass
    return None


# -------------------------
# Badge match alert
# -------------------------
def get_match_led_color(match_pct, rssi):
    """
    Decide badge-alert LED color:
    - strong match (>=60%) and close signal (>= -60 dBm): green
    - medium match (>=30%): cyan
    - weak match (<30%): amber
    """
    if match_pct >= 60 and rssi >= -60:
        return (0, 120, 0)
    if match_pct >= 30:
        return (0, 90, 90)
    return (100, 70, 0)


def flash_alert(color, flashes=2, on_s=0.08, off_s=0.08):
    for _ in range(flashes):
        pixels.fill(color)
        time.sleep(on_s)
        pixels.fill(0)
        time.sleep(off_s)


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
    _, match_pct = compute_match(MY_INTERESTS, peer_interests)
    shared = set(s.lower() for s in MY_INTERESTS) & set(s.lower() for s in peer_interests)
    if shared:
        color = get_match_led_color(match_pct, rssi)
        print(
            "ALERT! Shared badges with {}: {} ({}%, {} dBm, color={})".format(
                peer_info.get("name", ""),
                list(shared),
                match_pct,
                rssi,
                color,
            )
        )
        flash_alert(color)
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
    global display_dirty, chat_peer_mac, chat_common, chat_common_idx, contact_shared, chat_idx_ver
    global search_match_latched, search_match_topic, search_match_color

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
                old["name"] != info["name"] or
                old["topic"] != info["topic"]):
                changed = True

    # prune stale
    stale = [k for k, v in nearby_peers.items() if now - v["last_seen"] > PEER_TIMEOUT]
    for k in stale:
        del nearby_peers[k]
        changed = True

    if current_mode == MODE_CHAT:
        # Follow the strongest broadcaster in the same active topic to allow open join.
        if (not chat_force_empty_topic) and chat_common:
            active_topic = chat_common[chat_common_idx].lower()
            best_mac = None
            best_rssi = -999
            for mac, candidate in nearby_peers.items():
                if candidate.get("mode") != MODE_CHAT:
                    continue
                if candidate.get("topic", "").lower() != active_topic:
                    continue
                if candidate["rssi"] > best_rssi:
                    best_mac = mac
                    best_rssi = candidate["rssi"]
            if best_mac is not None:
                chat_peer_mac = best_mac

        peer = nearby_peers.get(chat_peer_mac) if chat_peer_mac else None
        if peer:
            new_common, _ = compute_match(MY_INTERESTS, peer["interests"])
            peer_topic = peer.get("topic", "")
            if (not chat_force_empty_topic) and peer_topic and peer_topic.lower() in set(s.lower() for s in MY_INTERESTS):
                if not any(c.lower() == peer_topic.lower() for c in new_common):
                    new_common = [peer_topic] + new_common
                else:
                    new_common = [c for c in new_common if c.lower() == peer_topic.lower()] + [
                        c for c in new_common if c.lower() != peer_topic.lower()
                    ]
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

    else:
        # Latch a match color in SEARCH mode and keep it until user enters CHAT.
        if not search_match_latched:
            matched_topic, _, _ = find_best_shared_match()
            if matched_topic:
                search_match_topic = matched_topic
                search_match_color = interest_to_led_color(matched_topic)
                search_match_latched = True

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
# -- LEDs --
def update_leds(phase):
    r, g, b = MODE_COLORS[current_mode]
    if current_mode == MODE_SEARCH:
        if search_match_latched:
            # Matched peer found: flash latched topic color until user enters CHAT.
            on = ((phase // 5) % 2) == 0
            pixels.fill(search_match_color if on else (0, 0, 0))
        else:
            n = len(nearby_peers)
            speed = max(20, 40 - n * 4)
            scale = abs((phase % speed) - speed // 2) / (speed / 2.0)
            br = int(g * (0.3 + 0.7 * scale))
            pixels.fill((0, br, 0))
    else:
        # In CHAT, keep LEDs solid in the shared-interest color.
        topic = ""
        if chat_common:
            topic = chat_common[chat_common_idx]
        elif search_match_topic:
            topic = search_match_topic

        if topic:
            pixels.fill(interest_to_led_color(topic))
        else:
            idx = (phase // 5) % 4
            pixels.fill((5, 4, 0))
            pixels[idx] = (min(r * 3, 255), min(g * 3, 255), 0)
            pixels[(idx + 2) % 4] = (min(r * 2, 255), min(g * 2, 255), 0)
    pixels.show()


def rssi_bar(rssi):
    if rssi > -50:
        return "***"
    if rssi > -70:
        return "**"
    return "*"


def _pack_interest_lines(interests, max_chars, max_lines=2, truncate=False):
    lines = []
    current = ""
    for raw in interests:
        item = (raw or "").strip()
        if not item:
            continue
        if len(item) > max_chars:
            item = item[:max(0, max_chars - 3)] + "..."

        part = item if not current else ", " + item
        if len(current) + len(part) <= max_chars:
            current += part
            continue

        if len(lines) >= (max_lines - 1):
            if not truncate:
                return None
            if len(current) > (max_chars - 3):
                current = current[:max(0, max_chars - 3)] + "..."
            else:
                suffix = ", ..."
                if len(current) + len(suffix) <= max_chars:
                    current += suffix
                else:
                    current = current[:max(0, max_chars - 3)] + "..."
            lines.append(current)
            return lines

        lines.append(current)
        current = item

    if current:
        lines.append(current)

    if len(lines) > max_lines:
        return None
    return lines


def get_badge_interest_layout(interests):
    items = [s for s in interests[:8] if s and s.strip()]
    if not items:
        return 1, ["(none)"]

    # Big text only when a single short item is present.
    if len(items) == 1 and len(items[0]) <= 20:
        return 2, [items[0]]

    # Otherwise use compact text and allow more wrapped content.
    lines = _pack_interest_lines(items, max_chars=46, max_lines=3, truncate=True)
    return 1, lines or ["(none)"]


# -- Display --
def render_display():
    global last_display_refresh, display_dirty

    epd = board.DISPLAY
    epd.rotation = 270

    g = displayio.Group()

    # background
    bg = displayio.Bitmap(296, 128, 1)
    pal = displayio.Palette(1)
    pal[0] = 0xFFFFFF
    g.append(displayio.TileGrid(bg, pixel_shader=pal))

    black_pal = displayio.Palette(1)
    black_pal[0] = 0x000000

    gray_pal = displayio.Palette(1)
    gray_pal[0] = 0x999999

    # divider
    bar = displayio.Bitmap(296, 3, 1)
    g.append(displayio.TileGrid(bar, pixel_shader=black_pal, x=0, y=24))

    # mode box
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

    # name (top right)
    g.append(label.Label(
        terminalio.FONT,
        text=(MY_NAME[:18]),
        color=0x000000,
        anchor_point=(1.0, 0.0),
        anchored_position=(290, 6),
        scale=1,
    ))

    search_text_scale = 2 if current_mode == MODE_SEARCH else 1

    # status line
    g.append(label.Label(
        terminalio.FONT,
        text=MODE_DESCRIPTIONS[current_mode],
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(6, 28 if current_mode == MODE_SEARCH else 30),
        scale=search_text_scale,
    ))

    y = 50 if current_mode == MODE_SEARCH else 42

    if current_mode == MODE_SEARCH:
        matched_topic = None
        matched_rssi = -999
        for _, peer in nearby_peers.items():
            topic = ""
            if peer.get("mode") == MODE_CHAT and peer.get("topic"):
                peer_topic = peer.get("topic", "")
                if any(peer_topic.lower() == mine.lower() for mine in MY_INTERESTS):
                    topic = peer_topic
            if not topic:
                topic = first_common_interest(MY_INTERESTS, peer.get("interests", []))
            if topic and peer.get("rssi", -999) > matched_rssi:
                matched_topic = topic
                matched_rssi = peer.get("rssi", -999)

        if matched_topic:
            g.append(label.Label(
                terminalio.FONT,
                text="Topic: " + matched_topic[:14 if search_text_scale == 2 else 30],
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=search_text_scale,
            ))
            y += 18 if search_text_scale == 2 else 12

        g.append(label.Label(
            terminalio.FONT,
            text="Nearby: " + str(len(nearby_peers)),
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, y),
            scale=search_text_scale,
        ))
        y += 18 if search_text_scale == 2 else 12

        if nearby_peers:
            max_peers = 2 if search_text_scale == 2 else 4
            if badge_visible:
                content_bottom = 76
                row_step = 17 if search_text_scale == 2 else 11
                room_rows = max(0, (content_bottom - y) // row_step)
                max_peers = min(max_peers, room_rows)
            for _, peer in sorted(nearby_peers.items(), key=lambda x: x[1]["rssi"], reverse=True)[:max_peers]:
                _, pct = compute_match(MY_INTERESTS, peer["interests"])
                line = "{} {}% {}".format(
                    peer["name"][:8 if search_text_scale == 2 else 10],
                    pct,
                    rssi_bar(peer["rssi"]),
                )
                g.append(label.Label(
                    terminalio.FONT,
                    text=line,
                    color=0x000000,
                    anchor_point=(0.0, 0.0),
                    anchored_position=(10, y),
                    scale=search_text_scale,
                ))
                y += 17 if search_text_scale == 2 else 11

        # badge toggle area
        if badge_visible:
            sep = displayio.Bitmap(296, 1, 1)
            g.append(displayio.TileGrid(sep, pixel_shader=gray_pal, x=0, y=78))
            g.append(label.Label(
                terminalio.FONT,
                text="Interests:",
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, 82),
                scale=1,
            ))
            badge_scale, badge_lines = get_badge_interest_layout(MY_INTERESTS)
            line_step = 16 if badge_scale == 2 else 9
            y_pos = 96 if badge_scale == 2 else 92
            max_bottom = 116
            glyph_h = 8 * badge_scale
            for row in badge_lines:
                if y_pos + glyph_h > max_bottom:
                    break
                g.append(label.Label(
                    terminalio.FONT,
                    text=row,
                    color=0x555555,
                    anchor_point=(0.0, 0.0),
                    anchored_position=(6, y_pos),
                    scale=badge_scale,
                ))
                y_pos += line_step
        g.append(label.Label(
            terminalio.FONT,
            text="[A] Chat  [B] Pairing  [C] Badge",
            color=0x333333,
            anchor_point=(0.5, 1.0),
            anchored_position=(148, 127),
            scale=1,
        ))

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

        topic = chat_common[chat_common_idx] if chat_common else ""
        idx_text = "({}/{})".format(chat_common_idx + 1, len(chat_common)) if chat_common else ""
        image_path = _topic_to_image_path(topic)
        image_drawn = False

        if topic:
            g.append(label.Label(
                terminalio.FONT,
                text="Topic: " + topic[:20],
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=1,
            ))
            if idx_text:
                g.append(label.Label(
                    terminalio.FONT,
                    text=idx_text,
                    color=0x555555,
                    anchor_point=(1.0, 0.0),
                    anchored_position=(290, y),
                    scale=1,
                ))
            y += 12

        if image_path:
            try:
                bmp = displayio.OnDiskBitmap(image_path)
                image_x = max(0, (296 - bmp.width) // 2)
                # Keep image a bit higher so footer instructions stay clear.
                image_y = max(y - 32, 32)
                max_bottom = 90
                if image_y + bmp.height > max_bottom:
                    image_y = max(-8, max_bottom - bmp.height)
                g.append(displayio.TileGrid(bmp, pixel_shader=bmp.pixel_shader, x=image_x, y=image_y))
                image_drawn = True
            except Exception:
                image_drawn = False

        if not image_drawn:
            fallback = "Common: " + topic if topic else "Common: (none)"
            g.append(label.Label(
                terminalio.FONT,
                text=fallback[:32],
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
            anchored_position=(6, 104),
            scale=1,
        ))

        g.append(label.Label(
            terminalio.FONT,
            text="[A] Back  [B] Share  [C] Next topic/image",
            color=0x333333,
            anchor_point=(0.5, 1.0),
            anchored_position=(148, 127),
            scale=1,
        ))

    epd.root_group = g
    time.sleep(epd.time_to_refresh + 0.01)
    epd.refresh()
    while epd.busy:
        pass

    last_display_refresh = time.monotonic()
    display_dirty = False

# -- Mode transitions --
def set_mode(new_mode, force_closest=False, force_empty_topic=False):
    global current_mode, display_dirty
    global chat_peer_mac, chat_common, chat_common_idx, chat_idx_ver, contact_shared, chat_force_empty_topic
    global search_match_latched, search_match_topic, search_match_color

    if new_mode == current_mode:
        return

    if new_mode == MODE_CHAT:
        # Clear search-match latch once user chooses to move into chat.
        search_match_latched = False
        chat_force_empty_topic = force_empty_topic
        chat_common_idx = 0
        chat_idx_ver = 0
        contact_shared = False
        chat_common = []

        if force_closest:
            chat_peer_mac = pick_closest_peer()
            if chat_peer_mac and chat_peer_mac in nearby_peers:
                chat_common, _ = compute_match(MY_INTERESTS, nearby_peers[chat_peer_mac]["interests"])
        else:
            # Prefer joining an ongoing chat that has a shared topic.
            best_mac = None
            best_rssi = -999
            best_topic = None
            mine_lower = set(s.lower() for s in MY_INTERESTS)
            for mac, peer in nearby_peers.items():
                topic = peer.get("topic", "")
                if peer.get("mode") == MODE_CHAT and topic and topic.lower() in mine_lower:
                    if peer["rssi"] > best_rssi:
                        best_mac = mac
                        best_rssi = peer["rssi"]
                        best_topic = topic

            if best_mac is not None:
                chat_peer_mac = best_mac
                chat_common = [best_topic]
            else:
                if chat_peer_mac is None:
                    chat_peer_mac = pick_closest_peer()
                if chat_peer_mac and chat_peer_mac in nearby_peers:
                    chat_common, _ = compute_match(MY_INTERESTS, nearby_peers[chat_peer_mac]["interests"])
    else:
        # Fresh search session starts with no latched match color.
        search_match_latched = False
        search_match_topic = ""
        search_match_color = (0, 0, 0)
        chat_peer_mac = None
        chat_common = []
        chat_common_idx = 0
        chat_idx_ver = 0
        contact_shared = False
        chat_force_empty_topic = False

    current_mode = new_mode

    # Small LED blink on mode change
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
            # D15: enter CHAT
            if not buttons[BTN_A].value:
                set_mode(MODE_CHAT)
                wait_release(BTN_A)

            # D14: closest-peer chat (no topic broadcast)
            elif not buttons[BTN_B].value:
                set_mode(MODE_CHAT, force_closest=True, force_empty_topic=True)
                wait_release(BTN_B)

            # D12: toggle badge display
            elif not buttons[BTN_C].value:
                badge_visible = not badge_visible
                display_dirty = True
                wait_release(BTN_C)

        else:  # MODE_CHAT
            # D15: back to SEARCH
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

            # D12: next common interest (synced)
            elif not buttons[BTN_C].value:
                if chat_common:
                    chat_common_idx = (chat_common_idx + 1) % len(chat_common)
                    chat_idx_ver += 1
                    display_dirty = True
                    do_broadcast()
                pixels.fill((60, 60, 60))
                time.sleep(0.12)
                pixels.fill(0)
                wait_release(BTN_C)

        # Periodic broadcast
        if now - last_broadcast >= BROADCAST_INTERVAL:
            do_broadcast()

        # Receive
        receive_all()

        # Refresh display (rate-limited)
        if display_dirty and (now - last_display_refresh >= DISPLAY_REFRESH):
            render_display()

        # LEDs
        update_leds(phase)
        phase = (phase + 1) % 200

        time.sleep(0.08)

except Exception as ex:
    # Blink NeoPixels red
    for _ in range(10):
        pixels.fill((255, 0, 0))
        time.sleep(0.15)
        pixels.fill(0)
        time.sleep(0.15)

    # Try to show error on E-Ink using the working refresh pattern
    try:
        epd = board.DISPLAY
        epd.rotation = 270

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

        epd.root_group = g
        time.sleep(epd.time_to_refresh + 0.01)
        epd.refresh()
        while epd.busy:
            pass
    except Exception:
        pass
