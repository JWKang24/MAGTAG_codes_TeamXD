import time
import os
import board
import displayio
import terminalio
import neopixel
import keypad
import espnow
import wifi
import adafruit_imageload
from adafruit_display_text import label
import server_match_client

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


def _get_env_float(key, default):
    v = os.getenv(key)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _get_env_bool(key, default=True):
    d = 1 if default else 0
    return _get_env_int(key, d) != 0

MY_NAME = _get_env_str("MY_NAME", "MagTag")
MY_INTERESTS = _get_env_str("MY_INTERESTS", "")
ESPNOW_CHANNEL = _get_env_int("ESPNOW_CHANNEL", 6)
ESPNOW_PEER_CHANNEL = _get_env_int("ESPNOW_PEER_CHANNEL", 0)
RECENT_CHAT_PEERS_TOML = "/recent_chat_peers.toml"
RECENT_CHAT_PEERS_KEY = "RECENT_CHATTED_MACS"
DEBUG_ESPNOW = (_get_env_int("DEBUG_ESPNOW", 0) != 0)


MATCH_ENABLE_SERVER = _get_env_bool("MATCH_ENABLE_SERVER", True)
MATCH_SERVER_BASE_URL = _get_env_str("MATCH_SERVER_BASE_URL", "")
MATCH_SERVER_APP_KEY = _get_env_str("MATCH_SERVER_APP_KEY", "")
MATCH_HTTP_TIMEOUT_S = _get_env_float("MATCH_HTTP_TIMEOUT_S", 2.0)
MATCH_OBSERVE_INTERVAL_S = _get_env_float("MATCH_OBSERVE_INTERVAL_S", 1.0)
MATCH_REQUEST_INTERVAL_S = _get_env_float("MATCH_REQUEST_INTERVAL_S", 3.0)
MATCH_ERROR_BACKOFF_S = _get_env_float("MATCH_ERROR_BACKOFF_S", 8.0)
MATCH_RSSI_RECHECK_DELTA = _get_env_int("MATCH_RSSI_RECHECK_DELTA", 8)
WIFI_SSID = _get_env_str("CIRCUITPY_WIFI_SSID", "")
WIFI_PASSWORD = _get_env_str("CIRCUITPY_WIFI_PASSWORD", "")

# Timing
BROADCAST_INTERVAL = 2.0
PEER_TIMEOUT = 15.0
DISPLAY_REFRESH = 5.0
MAX_MSG_LEN = 250
CHAT_HANDSHAKE_TIMEOUT = 30.0
CHAT_PEER_EXIT_TIMEOUT = 10.0
AUTO_CHAT_WINDOW = 60.0
AUTO_RECONNECT_DELAY = 60.0
AUTO_RECONNECT_DELAY_EXTENDED = 300.0
PAIR_HOLD_SECONDS = 1.0
LOOP_SLEEP_S = 0.04
RX_MAX_PACKETS_PER_TICK = 6
MAX_NETWORK_OPS_PER_TICK = 1

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
buttons = keypad.Keys(button_pins, value_when_pressed=False, pull=True)

BTN_A, BTN_B, BTN_C, BTN_D = 0, 1, 2, 3

btn_a_is_down = False
btn_a_down_since = 0.0
btn_a_hold_fired = False

# -- ESP-NOW setup --
wifi.radio.enabled = True
wifi.radio.start_ap(" ", "", channel=ESPNOW_CHANNEL, max_connections=0)
wifi.radio.stop_ap()

BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"
e = espnow.ESPNow(buffer_size=1024)
broadcast_peer = espnow.Peer(mac=BROADCAST_MAC, channel=ESPNOW_PEER_CHANNEL)
e.peers.append(broadcast_peer)

my_mac = wifi.radio.mac_address
MY_DEVICE_ID = server_match_client.make_device_id(my_mac)

# -- State --
current_mode = MODE_SEARCH
last_broadcast = 0.0
last_display_refresh = 0.0
display_dirty = True
last_debug_log = 0.0
tx_attempts = 0
tx_errors = 0
rx_packets = 0
parse_failures = 0
match_rr_cursor = 0

# Debug timing metrics (printed only when DEBUG_ESPNOW=1)
debug_loop_max_ms = 0.0
debug_server_call_max_ms = 0.0
debug_server_call_last_ms = 0.0
debug_rx_max_per_tick = 0
debug_rx_last_per_tick = 0
debug_button_events_max = 0
debug_button_events_last = 0
debug_network_ops_last = 0

# Non-blocking LED effect queue
led_effect_queue = []
active_led_effect = None

# Nearby peers
nearby_peers = {}
blocked_auto_rematch_peers = set()

# Server state
peer_server_state = {}
server_client = None
server_enabled = False
server_auth_failed = False
next_observe_sync = 0.0
self_interest_synced = False

# Chat state
chat_peer_mac = None
chat_common = []
chat_common_idx = 0
chat_idx_ver = 0
chat_force_empty_topic = False
chat_wait_peer_mac = None
chat_wait_deadline = 0.0
chat_peer_exit_deadline = 0.0

# Auto-rematch state per peer (keyed by MAC hex).
# window_deadline: live match window for case 2
# cooldown_until: temporary block expiry for case 1 / case 2
# had_chat_attempt: whether either side tried entering chat during the live window
auto_rematch_state = {}

# Search-mode match LED latch state
search_match_latched = False
search_match_peer_mac = None
search_match_peer_name = ""
search_match_color = (0, 0, 0)
search_match_topics = []
search_match_icon_filename = ""

# -- Badge match alert state --
RSSI_BADGE_THRESHOLD = -65
seen_badge_devices = set()

# -------------------------
# Helper functions
# -------------------------
def build_message():
    interests_str = ""
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
            if _peer_is_server_match(chat_peer_mac):
                shared_flag = "1"
        else:
            peer_mac_hex = ""
        idx_str = str(chat_common_idx)
        ver_str = str(chat_idx_ver)
    else:
        target_peer = _pick_best_server_match_peer()
        if isinstance(target_peer, (bytes, bytearray)):
            peer_mac_hex = target_peer.hex()
            shared_flag = "1"

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
        shared_flag = (parts[5].strip() == "1")
        common_idx = int(parts[6]) if parts[6] else 0
        idx_ver = int(parts[7]) if parts[7] else 0
        return {
            "mode": mode,
            "name": name,
            "interests": interests,
            "topic": topic,
            "peer_mac": peer_mac,
            "shared_flag": shared_flag,
            "common_idx": common_idx,
            "idx_ver": idx_ver,
        }
    except Exception:
        return None


def _queue_led_effect(color, flashes=2, on_s=0.08, off_s=0.08):
    if flashes <= 0:
        return
    led_effect_queue.append(
        {
            "color": color,
            "flashes_left": int(flashes),
            "on_s": float(on_s),
            "off_s": float(off_s),
            "phase": "on",
            "phase_until": 0.0,
        }
    )


def _led_effect_override_color(now):
    global active_led_effect

    if active_led_effect is None and led_effect_queue:
        active_led_effect = led_effect_queue.pop(0)
        active_led_effect["phase"] = "on"
        active_led_effect["phase_until"] = now + active_led_effect["on_s"]

    while active_led_effect is not None and now >= active_led_effect.get("phase_until", 0.0):
        if active_led_effect["phase"] == "on":
            active_led_effect["phase"] = "off"
            active_led_effect["phase_until"] = now + active_led_effect["off_s"]
        else:
            active_led_effect["flashes_left"] -= 1
            if active_led_effect["flashes_left"] <= 0:
                active_led_effect = None
                if led_effect_queue:
                    active_led_effect = led_effect_queue.pop(0)
                    active_led_effect["phase"] = "on"
                    active_led_effect["phase_until"] = now + active_led_effect["on_s"]
            else:
                active_led_effect["phase"] = "on"
                active_led_effect["phase_until"] = now + active_led_effect["on_s"]

    if active_led_effect is None:
        return None
    if active_led_effect["phase"] == "on":
        return active_led_effect["color"]
    return (0, 0, 0)


def _record_server_call_duration(started_at):
    global debug_server_call_last_ms, debug_server_call_max_ms
    elapsed_ms = (time.monotonic() - started_at) * 1000.0
    debug_server_call_last_ms = elapsed_ms
    if elapsed_ms > debug_server_call_max_ms:
        debug_server_call_max_ms = elapsed_ms


def _handle_button_inputs(now):
    global btn_a_is_down, btn_a_down_since, btn_a_hold_fired

    handled_events = 0

    event = buttons.events.get()
    while event is not None:
        handled_events += 1
        idx = event.key_number
        if idx == BTN_A:
            if event.pressed:
                btn_a_is_down = True
                btn_a_down_since = now
                btn_a_hold_fired = False
            else:
                was_down = btn_a_is_down
                hold_fired = btn_a_hold_fired
                btn_a_is_down = False
                btn_a_hold_fired = False
                if was_down and (not hold_fired):
                    if current_mode == MODE_SEARCH:
                        set_mode(MODE_CHAT)
                    else:
                        set_mode(MODE_SEARCH)
        event = buttons.events.get()

    if btn_a_is_down and (not btn_a_hold_fired) and current_mode == MODE_SEARCH:
        if (now - btn_a_down_since) >= PAIR_HOLD_SECONDS:
            btn_a_hold_fired = True
            set_mode(MODE_CHAT, force_closest=True, force_empty_topic=True)

    return handled_events

def index_for_topic(common_list, topic):
    """Return index of topic in common_list (case-insensitive), or None."""
    if not common_list or not topic:
        return None
    t = topic.lower()
    for i, item in enumerate(common_list):
        if item.lower() == t:
            return i
    return None


def _normalize_mac_hex(text):
    value = (text or "").strip().lower().replace(":", "").replace("-", "")
    if len(value) != 12:
        return None
    for ch in value:
        if ch not in "0123456789abcdef":
            return None
    return value


def _mac_bytes_to_hex(mac):
    if isinstance(mac, (bytes, bytearray)) and len(mac) == 6:
        return bytes(mac).hex()
    return None


def _is_blocked_peer_mac(mac):
    mac_hex = _mac_bytes_to_hex(mac)
    if (not mac_hex) or (mac_hex == _mac_bytes_to_hex(my_mac)):
        return False
    return bytes.fromhex(mac_hex) in blocked_auto_rematch_peers


def _track_match_window(mac, peer_info):
    mac_hex = _mac_bytes_to_hex(mac)
    my_hex = _mac_bytes_to_hex(my_mac)
    if (not mac_hex) or (mac_hex == my_hex):
        return
    if bytes.fromhex(mac_hex) in blocked_auto_rematch_peers:
        return
    if not is_shared_interest_peer(peer_info):
        return

    state = auto_rematch_state.get(mac_hex)
    if state is None:
        auto_rematch_state[mac_hex] = {
            "window_deadline": time.monotonic() + AUTO_CHAT_WINDOW,
            "cooldown_until": 0.0,
            "had_chat_attempt": False,
        }


def _start_auto_rematch_block(mac, cooldown_seconds):
    mac_hex = _mac_bytes_to_hex(mac)
    my_hex = _mac_bytes_to_hex(my_mac)
    if (not mac_hex) or (mac_hex == my_hex):
        return
    if bytes.fromhex(mac_hex) in blocked_auto_rematch_peers:
        return

    auto_rematch_state[mac_hex] = {
        "window_deadline": 0.0,
        "cooldown_until": time.monotonic() + cooldown_seconds,
        "had_chat_attempt": True,
    }


def _mark_chat_handshake_success(mac):
    mac_hex = _mac_bytes_to_hex(mac)
    if not mac_hex:
        return
    blocked_auto_rematch_peers.add(bytes.fromhex(mac_hex))
    _save_recent_chat_peers(blocked_auto_rematch_peers)
    if mac_hex in auto_rematch_state:
        del auto_rematch_state[mac_hex]


def _mark_chat_attempt(mac):
    mac_hex = _mac_bytes_to_hex(mac)
    my_hex = _mac_bytes_to_hex(my_mac)
    if (not mac_hex) or (mac_hex == my_hex):
        return
    state = auto_rematch_state.get(mac_hex)
    if state is None:
        state = {
            "window_deadline": time.monotonic() + AUTO_CHAT_WINDOW,
            "cooldown_until": 0.0,
            "had_chat_attempt": True,
        }
    else:
        state["had_chat_attempt"] = True
    auto_rematch_state[mac_hex] = state


def _load_recent_chat_peers():
    peers = set()
    try:
        with open(RECENT_CHAT_PEERS_TOML, "r") as fp:
            raw = fp.read()
    except OSError:
        _save_recent_chat_peers(set())
        return peers

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(RECENT_CHAT_PEERS_KEY):
            continue
        parts = line.split("=", 1)
        if len(parts) != 2:
            continue
        value = parts[1].strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        for item in value.split(","):
            normalized = _normalize_mac_hex(item)
            if normalized:
                peers.add(bytes.fromhex(normalized))
        break

    return peers


def _save_recent_chat_peers(peers):
    macs = []
    for mac in peers:
        mac_hex = _mac_bytes_to_hex(mac)
        if mac_hex:
            macs.append(mac_hex)
    macs.sort()

    data = '{}="{}"\n'.format(RECENT_CHAT_PEERS_KEY, ",".join(macs))
    try:
        with open(RECENT_CHAT_PEERS_TOML, "w") as fp:
            fp.write(data)
    except Exception as ex:
        print("WARN: cannot write {}: {}".format(RECENT_CHAT_PEERS_TOML, ex))

def _peer_confidence(mac):
    state = _get_peer_server_state(mac, create=False) or {}
    conf = state.get("confidence")
    if conf is None:
        return 0.0
    try:
        return float(conf)
    except Exception:
        return 0.0


def _normalize_topic_token(text):
    if not isinstance(text, str):
        return ""
    return " ".join(text.strip().split())


def _normalize_icon_filename(text):
    if not isinstance(text, str):
        return ""
    return text.strip()


def _parse_first_topic(raw):
    text = _normalize_topic_token(raw)
    if not text:
        return ""

    parts = [text]
    for delim in ("|", ",", ";"):
        if delim in text:
            parts = text.split(delim)
            break

    for part in parts:
        topic = _normalize_topic_token(part)
        if not topic:
            continue
        return topic
    return ""


def _topic_list_from_raw(raw):
    topic = _parse_first_topic(raw)
    return [topic] if topic else []


def _peer_server_topics(mac):
    state = _get_peer_server_state(mac, create=False) or {}
    single = _normalize_topic_token(state.get("topic"))
    if single:
        return [single]

    raw_topics = state.get("topics")
    if isinstance(raw_topics, list):
        for item in raw_topics:
            topic = _normalize_topic_token(item)
            if topic:
                return [topic]
    return []


def _peer_broadcast_topics(mac):
    peer = nearby_peers.get(mac)
    if not isinstance(peer, dict):
        return []
    return _topic_list_from_raw(peer.get("topic"))


def _resolve_topics_for_peer(mac):
    topics = _peer_server_topics(mac)
    if topics:
        return topics[:1]
    topics = _peer_broadcast_topics(mac)
    if topics:
        return topics[:1]
    return ["Conversation"]


def _resolve_search_topics_for_peer(mac):
    topics = _peer_server_topics(mac)
    if topics:
        return topics[:1]
    topics = _peer_broadcast_topics(mac)
    if topics:
        return topics[:1]
    return []


def _topics_debug_str(topics):
    for item in topics[:1]:
        topic = _normalize_topic_token(item)
        if topic:
            return topic
    return "-"


def _topic_source_for_peer(mac):
    if _peer_server_topics(mac):
        return "server"
    if _peer_broadcast_topics(mac):
        return "peer"
    return "none"


def _pick_best_server_match_peer(require_peer_targets_me=False):
    best_mac = None
    best_conf = -1.0
    best_mac_hex = None
    my_mac_bytes = bytes(my_mac)

    for mac, peer in nearby_peers.items():
        if mac == my_mac_bytes:
            continue
        if _is_blocked_peer_mac(mac):
            continue
        if not _peer_is_server_match(mac):
            continue
        if require_peer_targets_me:
            if not peer.get("shared_flag"):
                continue
            if peer.get("peer_mac") != my_mac_bytes:
                continue

        conf = _peer_confidence(mac)
        mac_hex = _mac_bytes_to_hex(mac) or ""
        if (
            best_mac is None
            or conf > best_conf
            or (conf == best_conf and mac_hex < best_mac_hex)
        ):
            best_mac = mac
            best_conf = conf
            best_mac_hex = mac_hex

    return best_mac


def _pair_led_color(mac_a, mac_b):
    mac_a_hex = _mac_bytes_to_hex(mac_a)
    mac_b_hex = _mac_bytes_to_hex(mac_b)
    if (not mac_a_hex) or (not mac_b_hex):
        return (0, 80, 80)

    if mac_a_hex > mac_b_hex:
        mac_a_hex, mac_b_hex = mac_b_hex, mac_a_hex

    pair_key = "{}:{}".format(mac_a_hex, mac_b_hex)
    h = 0
    for ch in pair_key:
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


def _server_icon_to_image_path(icon_filename):
    if not isinstance(icon_filename, str):
        return None
    name = icon_filename.strip()
    if not name:
        return None
    if "/" in name or "\\" in name:
        return None
    path = "/images/{}".format(name)
    try:
        os.stat(path)
        return path
    except OSError:
        return None


def _resolve_topic_image_path(topic, icon_filename):
    from_server = _server_icon_to_image_path(icon_filename)
    if from_server:
        return from_server
    return _topic_to_image_path(topic)


def _wrap_text_for_panel(text, max_chars=11, max_lines=3):
    raw = _normalize_topic_token(text)
    if not raw:
        return ""

    words = raw.split(" ")
    if not words:
        return ""

    lines = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            if len(lines) >= max_lines:
                break
            current = word
    if len(lines) < max_lines:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return "\n".join(lines)


def _topic_sentence_chat(topic):
    clean = _normalize_topic_token(topic)
    if not clean:
        return ""
    return "You are both interested in {}".format(clean)


def _fit_bitmap_to_box(bitmap, palette, max_width, max_height):
    if (
        bitmap.width <= max_width
        and bitmap.height <= max_height
    ):
        return bitmap, palette

    factor = 1
    while (
        ((bitmap.width + factor - 1) // factor) > max_width
        or ((bitmap.height + factor - 1) // factor) > max_height
    ):
        factor += 1

    new_w = max(1, bitmap.width // factor)
    new_h = max(1, bitmap.height // factor)
    color_count = 2
    try:
        color_count = len(palette)
    except Exception:
        color_count = 2
    if color_count < 2:
        color_count = 2

    resized = displayio.Bitmap(new_w, new_h, color_count)
    for y in range(new_h):
        src_y = y * factor
        if src_y >= bitmap.height:
            src_y = bitmap.height - 1
        for x in range(new_w):
            src_x = x * factor
            if src_x >= bitmap.width:
                src_x = bitmap.width - 1
            resized[x, y] = bitmap[src_x, src_y]
    return resized, palette


def _render_topic_visual_panel(
    group,
    start_y,
    topics,
    icon_filename=None,
    sentence_mode=False,
    max_bottom=112,
    text_scale=2,
):
    topic = ""
    for item in topics[:1]:
        normalized = _normalize_topic_token(item)
        if normalized:
            topic = normalized
            break

    if not topic:
        return False, start_y, None

    image_path = _resolve_topic_image_path(topic, icon_filename)
    if not image_path:
        return False, start_y, None

    try:
        bitmap, palette = adafruit_imageload.load(
            image_path,
            bitmap=displayio.Bitmap,
            palette=displayio.Palette,
        )

        left_panel_width = 296 // 2
        right_panel_x = left_panel_width + 12
        top_y = max(start_y, 30)
        max_image_width = left_panel_width - 8
        max_image_height = max(16, max_bottom - top_y)
        bitmap, palette = _fit_bitmap_to_box(
            bitmap,
            palette,
            max_image_width,
            max_image_height,
        )
        if top_y + bitmap.height > max_bottom:
            top_y = max(22, max_bottom - bitmap.height)

        image_x = max(0, (left_panel_width - bitmap.width) // 2)
        group.append(displayio.TileGrid(bitmap, pixel_shader=palette, x=image_x, y=top_y))

        caption = _topic_sentence_chat(topic) if sentence_mode else topic
        wrap_chars = 11 if text_scale >= 2 else 18
        wrapped = _wrap_text_for_panel(caption, max_chars=wrap_chars, max_lines=3)
        line_count = 1
        if wrapped:
            line_count = wrapped.count("\n") + 1
        group.append(label.Label(
            terminalio.FONT,
            text=wrapped,
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(right_panel_x, top_y + 2),
            scale=text_scale,
            line_spacing=1.1,
        ))
        text_bottom = top_y + 2 + (line_count * 10 * text_scale)
        panel_bottom = top_y + bitmap.height
        if text_bottom > panel_bottom:
            panel_bottom = text_bottom
        if panel_bottom > max_bottom:
            panel_bottom = max_bottom
        return True, panel_bottom, image_path
    except Exception:
        return False, start_y, None


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
    _queue_led_effect(color, flashes=flashes, on_s=on_s, off_s=off_s)



def check_badge_matches(packet_mac, peer_info):
    global seen_badge_devices
    if packet_mac == bytes(my_mac):
        return
    if _is_blocked_peer_mac(packet_mac):
        return
    if packet_mac in seen_badge_devices:
        return

    state = _get_peer_server_state(packet_mac, create=False)
    if not state:
        return
    if not state.get("local_gate"):
        return
    if state.get("decision") is not True:
        return

    rssi = peer_info.get("rssi", -100)
    if rssi < RSSI_BADGE_THRESHOLD:
        return

    confidence = state.get("confidence")
    if confidence is None:
        confidence = 0.0
    match_pct = int(max(0, min(100, confidence * 100.0)))
    color = get_match_led_color(match_pct, rssi)
    server_topics = _peer_server_topics(packet_mac)
    peer_topics = _peer_broadcast_topics(packet_mac)
    topic_source = _topic_source_for_peer(packet_mac)
    chosen_topic = server_topics[0] if server_topics else (peer_topics[0] if peer_topics else "")
    icon_filename = state.get("icon_filename")
    resolved_image = _resolve_topic_image_path(chosen_topic, icon_filename) if chosen_topic else None
    print(
        (
            "ALERT! Server match with {}: conf={}%, rssi={} dBm, color={} "
            "topic_src={} topic={} server_icon={} image={} "
            "server_topics={} peer_topics={}"
        ).format(
            peer_info.get("name", ""),
            match_pct,
            rssi,
            color,
            topic_source,
            chosen_topic or "-",
            icon_filename or "-",
            resolved_image or "-",
            _topics_debug_str(server_topics),
            _topics_debug_str(peer_topics),
        )
    )
    flash_alert(color)
    seen_badge_devices.add(packet_mac)


def is_shared_interest_peer(peer_info):
    _ = peer_info
    return True
# -------------------------
# Broadcast / receive
# -------------------------
def do_broadcast():
    global last_broadcast, tx_attempts, tx_errors
    msg = build_message()
    tx_attempts += 1
    try:
        e.send(bytes(msg, "utf-8"), broadcast_peer)
    except Exception as ex:
        tx_errors += 1
        if DEBUG_ESPNOW:
            print("ESPNOW TX error:", ex)
    last_broadcast = time.monotonic()

def flash_new_peer():
    _queue_led_effect((0, 80, 80), flashes=2, on_s=0.08, off_s=0.08)

def receive_all(max_packets=RX_MAX_PACKETS_PER_TICK):
    global display_dirty, chat_peer_mac, chat_common, chat_common_idx, chat_idx_ver
    global search_match_latched, search_match_peer_mac, search_match_peer_name, search_match_color
    global search_match_topics, search_match_icon_filename
    global chat_wait_peer_mac, chat_wait_deadline, chat_peer_exit_deadline
    global rx_packets, parse_failures

    changed = False
    processed = 0
    now = time.monotonic()

    while e:
        if max_packets and processed >= max_packets:
            break
        packet = e.read()
        if packet is None:
            break
        processed += 1
        rx_packets += 1

        info = parse_message(packet.msg)
        if info is None:
            parse_failures += 1
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
            "shared_flag": info["shared_flag"],
            "common_idx": info["common_idx"],
            "idx_ver": info["idx_ver"],
        }
        _track_match_window(mac_key, nearby_peers[mac_key])
        is_blocked_peer = _is_blocked_peer_mac(mac_key)

        # --- badge match alert ---
        if not is_blocked_peer:
            check_badge_matches(mac_key, nearby_peers[mac_key])

        if old is None:
            changed = True
            if (not is_blocked_peer) and _peer_is_server_match(mac_key):
                flash_new_peer()
        else:
            if (old["mode"] != info["mode"] or
                old["name"] != info["name"] or
                old["topic"] != info["topic"] or
                old.get("peer_mac") != info["peer_mac"] or
                old.get("shared_flag") != info["shared_flag"]):
                changed = True
            # Peer timed out/exited CHAT that was targeting us:
            # mirror cooldown on this badge so SEARCH match notice clears too.
            if (old.get("mode") == MODE_CHAT and
                    info["mode"] == MODE_SEARCH and
                    old.get("peer_mac") == bytes(my_mac)):
                _start_auto_rematch_block(mac_key, AUTO_RECONNECT_DELAY)
                if current_mode == MODE_CHAT and chat_peer_mac == mac_key:
                    chat_peer_exit_deadline = time.monotonic() + CHAT_PEER_EXIT_TIMEOUT
                changed = True

    # prune stale
    stale = [k for k, v in nearby_peers.items() if now - v["last_seen"] > PEER_TIMEOUT]
    for k in stale:
        del nearby_peers[k]
        changed = True

    if current_mode == MODE_CHAT:
        peer = nearby_peers.get(chat_peer_mac) if chat_peer_mac else None
        if peer:
            peer_in_chat = (peer.get("mode") == MODE_CHAT)
            if peer_in_chat:
                if chat_peer_mac:
                    _mark_chat_handshake_success(chat_peer_mac)
                    if (
                        chat_wait_peer_mac == chat_peer_mac and
                        peer.get("peer_mac") == bytes(my_mac)
                    ):
                        chat_wait_deadline = 0.0
                    if chat_peer_exit_deadline > 0.0 and peer.get("peer_mac") == bytes(my_mac):
                        chat_peer_exit_deadline = 0.0

    else:
        best_mac = _pick_best_server_match_peer()
        if best_mac is not None:
            best_peer_name = nearby_peers.get(best_mac, {}).get("name", "")
            new_color = _pair_led_color(bytes(my_mac), best_mac)
            new_topics = _resolve_search_topics_for_peer(best_mac)
            best_state = _get_peer_server_state(best_mac, create=False) or {}
            new_icon_filename = _normalize_icon_filename(best_state.get("icon_filename"))
            if (not search_match_latched or
                    best_mac != search_match_peer_mac or
                    best_peer_name != search_match_peer_name or
                    new_color != search_match_color or
                    new_topics != search_match_topics or
                    new_icon_filename != search_match_icon_filename):
                changed = True
            search_match_peer_mac = best_mac
            search_match_peer_name = best_peer_name
            search_match_color = new_color
            search_match_topics = new_topics
            search_match_icon_filename = new_icon_filename
            search_match_latched = True
        else:
            if search_match_latched or search_match_peer_mac or search_match_topics or search_match_icon_filename:
                changed = True
            search_match_latched = False
            search_match_peer_mac = None
            search_match_peer_name = ""
            search_match_color = (0, 0, 0)
            search_match_topics = []
            search_match_icon_filename = ""

    if changed:
        display_dirty = True
    return processed

# -------------------------
# Pick closest peer
# -------------------------
def pick_closest_peer(skip_blocked=False):
    best_mac = None
    best_rssi = -999
    for mac, peer in nearby_peers.items():
        if skip_blocked and _is_blocked_peer_mac(mac):
            continue
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
    override = _led_effect_override_color(time.monotonic())
    if override is not None:
        pixels.fill(override)
    else:
        if current_mode == MODE_SEARCH:
            if (
                search_match_latched and
                search_match_peer_mac is not None and
                _peer_is_server_match(search_match_peer_mac)
            ):
                # Matched peer found: root-pattern flash cadence.
                on = ((phase // 5) % 2) == 0
                pixels.fill(search_match_color if on else (0, 0, 0))
            else:
                # No active match: keep a steady search color (no flashing).
                pixels.fill((0, 12, 0))
        else:
            if chat_peer_mac is not None and _peer_is_server_match(chat_peer_mac):
                pixels.fill(_pair_led_color(bytes(my_mac), chat_peer_mac))
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


def _display_interest_text(text):
    value = (text or "").replace("_", " ").strip().lower()
    if not value:
        return ""
    words = [w for w in value.split(" ") if w]
    return " ".join(w[0].upper() + w[1:] for w in words)


def _pack_interest_lines(interests, max_chars, max_lines=2, truncate=False):
    lines = []
    current = ""
    for raw in interests:
        item = _display_interest_text(raw)
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
        return 1, ["(None)"]

    for scale in (2, 1):
        max_chars = 23 if scale == 2 else 46
        lines = _pack_interest_lines(items, max_chars=max_chars, max_lines=2, truncate=False)
        if lines is not None:
            return scale, lines

    lines = _pack_interest_lines(items, max_chars=46, max_lines=2, truncate=True)
    return 1, lines or ["(None)"]


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
    search_match_active = (
        current_mode == MODE_SEARCH
        and search_match_latched
        and search_match_peer_mac is not None
        and _peer_is_server_match(search_match_peer_mac)
    )
    show_status_line = (current_mode == MODE_SEARCH) and (not search_match_active)

    if show_status_line:
        g.append(label.Label(
            terminalio.FONT,
            text=MODE_DESCRIPTIONS[MODE_SEARCH],
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, 28),
            scale=search_text_scale,
        ))

    y = 50 if (current_mode == MODE_SEARCH and show_status_line) else 32 if current_mode == MODE_SEARCH else 30
    content_bottom = 112

    if current_mode == MODE_SEARCH:
        search_match_label_scale = 1 if search_text_scale > 1 else search_text_scale

        if search_match_peer_name:
            g.append(label.Label(
                terminalio.FONT,
                text="Match: " + search_match_peer_name[:24],
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=search_match_label_scale,
            ))
            y += 12 if search_match_label_scale == 1 else 18

        if search_match_topics:
            rendered, panel_bottom, _ = _render_topic_visual_panel(
                g,
                y,
                search_match_topics,
                search_match_icon_filename,
                sentence_mode=False,
                max_bottom=116,
                text_scale=2,
            )
            if rendered:
                y = panel_bottom + 4
            else:
                topic_text = _display_interest_text(search_match_topics[0])
                if topic_text:
                    wrapped_topic = _wrap_text_for_panel(topic_text, max_chars=14, max_lines=2)
                    topic_line_count = wrapped_topic.count("\n") + 1 if wrapped_topic else 0
                    g.append(label.Label(
                        terminalio.FONT,
                        text=wrapped_topic[:64],
                        color=0x000000,
                        anchor_point=(0.0, 0.0),
                        anchored_position=(6, y),
                        scale=3,
                        line_spacing=1.1,
                    ))
                    y += max(27, topic_line_count * 27)

        if y <= (content_bottom - (18 if search_text_scale == 2 else 12)):
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
            row_step = 17 if search_text_scale == 2 else 11
            room_rows = max(0, (content_bottom - y) // row_step)
            max_peers = min(max_peers, room_rows)
            for mac, peer in sorted(nearby_peers.items(), key=lambda x: x[1]["rssi"], reverse=True)[:max_peers]:
                status = _peer_status_text(mac)
                line = "{} {} {}".format(
                    peer["name"][:8 if search_text_scale == 2 else 10],
                    status,
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

        g.append(label.Label(
            terminalio.FONT,
            text="[A] Chat  [Hold A] Pair",
            color=0x333333,
            anchor_point=(0.5, 1.0),
            anchored_position=(148, 127),
                scale=1,
            ))

    else:
        peer_name = "(None)"
        peer_in_chat = False
        if chat_peer_mac and chat_peer_mac in nearby_peers:
            peer_name = nearby_peers[chat_peer_mac]["name"][:16]
            peer_in_chat = (
                nearby_peers[chat_peer_mac].get("mode") == MODE_CHAT and
                nearby_peers[chat_peer_mac].get("peer_mac") == bytes(my_mac)
            )

        g.append(label.Label(
            terminalio.FONT,
            text=("Chatting With: " + peer_name)[:40],
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(6, y),
            scale=1,
        ))
        y += 12

        chat_topics = chat_common[:1] if (chat_common and peer_in_chat) else []
        topic_text = _display_interest_text(chat_topics[0]) if chat_topics else ""
        idx_text = "({}/{})".format(chat_common_idx + 1, len(chat_common)) if chat_common else ""
        image_drawn = False
        chat_icon_filename = ""
        if chat_peer_mac is not None:
            chat_state = _get_peer_server_state(chat_peer_mac, create=False) or {}
            chat_icon_filename = chat_state.get("icon_filename") or ""

        if chat_topics:
            rendered, panel_bottom, _ = _render_topic_visual_panel(
                g,
                y,
                chat_topics,
                chat_icon_filename,
                sentence_mode=True,
                max_bottom=116,
                text_scale=1,
            )
            if rendered:
                image_drawn = True
                y = panel_bottom + 4

        if not image_drawn:
            if topic_text:
                fallback = _topic_sentence_chat(topic_text)
            elif peer_in_chat:
                fallback = "Conversation"
            else:
                fallback = "Waiting: peer press A"

            max_lines = 3
            if y > 90:
                max_lines = 2
            if y > 104:
                max_lines = 1
            wrapped_fallback = _wrap_text_for_panel(fallback, max_chars=18, max_lines=max_lines)
            fallback_line_count = wrapped_fallback.count("\n") + 1 if wrapped_fallback else 0
            g.append(label.Label(
                terminalio.FONT,
                text=wrapped_fallback[:64],
                color=0x000000,
                anchor_point=(0.0, 0.0),
                anchored_position=(6, y),
                scale=2,
                line_spacing=1.1,
            ))
            y += max(18, fallback_line_count * 18)

            if idx_text and y <= (content_bottom - 10):
                g.append(label.Label(
                    terminalio.FONT,
                    text=idx_text,
                    color=0x555555,
                    anchor_point=(0.0, 0.0),
                    anchored_position=(6, y),
                    scale=1,
                ))
                y += 12

        g.append(label.Label(
            terminalio.FONT,
            text="[A] Back",
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
    global chat_peer_mac, chat_common, chat_common_idx, chat_idx_ver, chat_force_empty_topic
    global chat_wait_peer_mac, chat_wait_deadline, chat_peer_exit_deadline
    global search_match_latched, search_match_peer_mac, search_match_peer_name, search_match_color
    global search_match_topics, search_match_icon_filename
    global blocked_auto_rematch_peers

    if new_mode == current_mode:
        return

    if new_mode == MODE_CHAT:
        if force_closest:
            selected_peer = pick_closest_peer(skip_blocked=False)
        else:
            selected_peer = _pick_best_server_match_peer(require_peer_targets_me=True)
            if selected_peer is None:
                selected_peer = _pick_best_server_match_peer()

        if selected_peer is None:
            return

        chat_wait_deadline = time.monotonic() + CHAT_HANDSHAKE_TIMEOUT
        chat_wait_peer_mac = selected_peer
        chat_peer_exit_deadline = 0.0

        search_match_latched = False
        search_match_peer_mac = None
        search_match_peer_name = ""
        search_match_color = (0, 0, 0)
        search_match_topics = []
        search_match_icon_filename = ""

        chat_peer_mac = selected_peer
        chat_force_empty_topic = force_empty_topic
        chat_common_idx = 0
        chat_idx_ver = 0
        if chat_force_empty_topic:
            chat_common = []
        else:
            chat_common = _resolve_topics_for_peer(chat_peer_mac)
        _mark_chat_attempt(chat_peer_mac)
    else:
        if chat_peer_mac is not None:
            _start_auto_rematch_block(chat_peer_mac, AUTO_RECONNECT_DELAY)

        # Fresh search session starts with no latched match color.
        search_match_latched = False
        search_match_peer_mac = None
        search_match_peer_name = ""
        search_match_color = (0, 0, 0)
        search_match_topics = []
        search_match_icon_filename = ""
        chat_peer_mac = None
        chat_common = []
        chat_common_idx = 0
        chat_idx_ver = 0
        chat_force_empty_topic = False
        chat_wait_peer_mac = None
        chat_wait_deadline = 0.0
        chat_peer_exit_deadline = 0.0

    current_mode = new_mode

    # Small LED blink on mode change
    pixels.fill(MODE_COLORS[new_mode])
    time.sleep(0.15)
    pixels.fill(0)

    display_dirty = True
    do_broadcast()


blocked_auto_rematch_peers = _load_recent_chat_peers()


def _new_peer_server_state():
    return {
        "local_gate": True,
        "decision": None,
        "confidence": None,
        "source": None,
        "topic": "",
        "topics": [],
        "icon_filename": "",
        "eligible": None,
        "reason": None,
        "next_try": 0.0,
        "last_error": "",
        "last_match_ts": 0.0,
        "last_match_rssi": None,
    }


def _get_peer_server_state(mac, create=True):
    state = peer_server_state.get(mac)
    if state is None and create:
        state = _new_peer_server_state()
        peer_server_state[mac] = state
    return state


def _peer_is_server_match(mac):
    state = _get_peer_server_state(mac, create=False)
    if not state:
        return False
    return bool(state.get("decision") is True)


def _is_transient_server_error(code):
    c = str(code or "").upper()
    if not c:
        return True
    if c == "NETWORK_ERROR":
        return True
    if c.startswith("HTTP_5"):
        return True
    if c in ("HTTP_429", "LLM_UPSTREAM_ERROR", "LLM_UPSTREAM_TIMEOUT", "LLM_RESPONSE_INVALID", "LLM_RATE_LIMIT"):
        return True
    return False


def _peer_status_text(mac):
    state = _get_peer_server_state(mac, create=False)
    if not state:
        return "WAIT"
    decision = state.get("decision")
    if decision is True:
        conf = state.get("confidence")
        if conf is None:
            return "YES"
        return "{}%".format(int(max(0, min(99, conf * 100.0))))
    if decision is False:
        return "NO"
    err_code = str(state.get("last_error") or "")
    if err_code and (not _is_transient_server_error(err_code)):
        return "ERR"
    return "WAIT"


def _sync_local_gate_cache():
    active = set(nearby_peers.keys())
    stale = [k for k in peer_server_state if k not in active]
    for k in stale:
        del peer_server_state[k]

    for mac, _peer in nearby_peers.items():
        state = _get_peer_server_state(mac, create=True)
        state["local_gate"] = True


def _ensure_wifi_connected():
    try:
        if wifi.radio.ipv4_address:
            return True
    except Exception:
        pass

    if (not WIFI_SSID) or (not WIFI_PASSWORD):
        return False

    try:
        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
        return bool(wifi.radio.ipv4_address)
    except Exception:
        return False


def _initialize_server_client(now):
    global server_client, server_enabled, next_observe_sync

    if server_client is not None:
        return

    if not MATCH_ENABLE_SERVER:
        print("SERVER disabled by MATCH_ENABLE_SERVER")
        server_enabled = False
        return

    if (not MATCH_SERVER_BASE_URL) or (not MATCH_SERVER_APP_KEY):
        print("SERVER disabled: missing MATCH_SERVER_BASE_URL or MATCH_SERVER_APP_KEY")
        server_enabled = False
        return

    base_url_low = MATCH_SERVER_BASE_URL.strip().lower()
    if (
        "://0.0.0.0" in base_url_low
        or "://127.0.0.1" in base_url_low
        or "://localhost" in base_url_low
        or "://[::1]" in base_url_low
    ):
        print(
            "SERVER disabled: MATCH_SERVER_BASE_URL is not reachable from badge; use laptop LAN IP."
        )
        server_enabled = False
        return

    if not _ensure_wifi_connected():
        print("SERVER disabled: Wi-Fi station not connected")
        server_enabled = False
        return

    try:
        server_client = server_match_client.ServerMatchClient(
            base_url=MATCH_SERVER_BASE_URL,
            app_key=MATCH_SERVER_APP_KEY,
            timeout_s=MATCH_HTTP_TIMEOUT_S,
        )
        server_enabled = True
        next_observe_sync = now
        _sync_self_interest()
        print("SERVER enabled base_url={} device_id={}".format(MATCH_SERVER_BASE_URL, MY_DEVICE_ID))
    except Exception as ex:
        server_client = None
        server_enabled = False
        print("SERVER init error: {}".format(ex))


def _mark_server_error(result):
    global server_auth_failed
    code = str(result.get("error_code") or "")
    if code == "UNAUTHORIZED":
        server_auth_failed = True
    return code


def _sync_self_interest():
    global self_interest_synced

    if self_interest_synced or (not server_enabled) or server_auth_failed or server_client is None:
        return

    interest_blurb = (MY_INTERESTS or "").strip()
    if not interest_blurb:
        self_interest_synced = True
        return

    result = server_client.put_interest(MY_DEVICE_ID, interest_blurb)
    if result.get("ok"):
        self_interest_synced = True
        print("SERVER self-interest synced")
        return

    code = _mark_server_error(result)
    print("SERVER self-interest sync failed code={}".format(code or "UNKNOWN"))
    self_interest_synced = True


def _sync_server_observations(now):
    global next_observe_sync

    if not server_enabled or server_auth_failed or server_client is None:
        return False
    if now < next_observe_sync:
        return False

    observations = []
    for mac, peer in nearby_peers.items():
        state = _get_peer_server_state(mac, create=True)
        if state.get("decision") is False and now < float(state.get("next_try") or 0.0):
            continue

        target_device_id = _mac_bytes_to_hex(mac)
        if not target_device_id:
            continue

        observations.append(
            {
                "target_device_id": target_device_id,
                "signal_type": "rssi",
                "signal_value": int(peer.get("rssi", -100)),
            }
        )

    if not observations:
        next_observe_sync = now + MATCH_OBSERVE_INTERVAL_S
        return False

    started = time.monotonic()
    result = server_client.post_observe(MY_DEVICE_ID, observations)
    _record_server_call_duration(started)
    if result.get("ok"):
        next_observe_sync = now + MATCH_OBSERVE_INTERVAL_S
        return True

    code = _mark_server_error(result)
    next_observe_sync = now + MATCH_ERROR_BACKOFF_S
    print("SERVER observe failed code={}".format(code or "UNKNOWN"))
    return True


def _peer_due_for_server_match(state, peer, now):
    next_try = float(state.get("next_try") or 0.0)
    if now >= next_try:
        return True

    last_rssi = state.get("last_match_rssi")
    if last_rssi is None:
        return False

    delta = abs(int(peer.get("rssi", -100)) - int(last_rssi))
    return delta >= MATCH_RSSI_RECHECK_DELTA


def _sync_server_matches(now, max_calls=1):
    global match_rr_cursor

    if not server_enabled or server_auth_failed or server_client is None:
        return 0
    if max_calls <= 0:
        return 0

    peer_items = list(nearby_peers.items())
    if not peer_items:
        match_rr_cursor = 0
        return 0

    n = len(peer_items)
    start_idx = match_rr_cursor % n
    checked = 0
    calls = 0

    while checked < n and calls < max_calls:
        idx = (start_idx + checked) % n
        mac, peer = peer_items[idx]
        state = _get_peer_server_state(mac, create=True)

        if not _peer_due_for_server_match(state, peer, now):
            checked += 1
            continue

        peer_device_id = _mac_bytes_to_hex(mac)
        if not peer_device_id:
            checked += 1
            continue

        started = time.monotonic()
        result = server_client.post_match(
            MY_DEVICE_ID,
            peer_device_id,
        )
        _record_server_call_duration(started)
        calls += 1
        match_rr_cursor = (idx + 1) % n

        if result.get("ok"):
            data = result.get("data")
            if not isinstance(data, dict):
                data = {}

            eligibility = data.get("eligibility")
            if not isinstance(eligibility, dict):
                eligibility = {}

            old_decision = state.get("decision")
            incoming_decision = data.get("decision")
            if incoming_decision is None and old_decision is False:
                # Keep a confirmed NO sticky even when later requests are temporarily gated.
                state["decision"] = False
            else:
                state["decision"] = incoming_decision
                state["confidence"] = data.get("confidence")
                state["source"] = data.get("source")
                parsed_topics = _topic_list_from_raw(data.get("topic"))
                state["topics"] = parsed_topics
                state["topic"] = parsed_topics[0] if parsed_topics else ""
                state["icon_filename"] = _normalize_icon_filename(data.get("icon_filename"))
            state["eligible"] = eligibility.get("eligible")
            state["reason"] = eligibility.get("reason")
            state["last_error"] = ""
            state["last_match_ts"] = now
            state["last_match_rssi"] = int(peer.get("rssi", -100))
            if state["decision"] is False:
                # Do not actively re-query known non-matches while they stay nearby.
                state["next_try"] = now + max(60.0, MATCH_REQUEST_INTERVAL_S * 10.0)
            else:
                state["next_try"] = now + MATCH_REQUEST_INTERVAL_S

            if old_decision != state.get("decision"):
                server_topics = _peer_server_topics(mac)
                peer_topics = _peer_broadcast_topics(mac)
                topic_source = _topic_source_for_peer(mac)
                chosen_topic = server_topics[0] if server_topics else (peer_topics[0] if peer_topics else "")
                icon_filename = state.get("icon_filename")
                resolved_image = _resolve_topic_image_path(chosen_topic, icon_filename) if chosen_topic else None
                print(
                    (
                        "SERVER_MATCH {} decision={} source={} conf={} "
                        "topic_src={} topic={} server_icon={} image={} "
                        "server_topics={} peer_topics={}"
                    ).format(
                        _mac_bytes_to_hex(mac),
                        state.get("decision"),
                        state.get("source"),
                        state.get("confidence"),
                        topic_source,
                        chosen_topic or "-",
                        icon_filename or "-",
                        resolved_image or "-",
                        _topics_debug_str(server_topics),
                        _topics_debug_str(peer_topics),
                    )
                )
        else:
            code = _mark_server_error(result)
            if _is_transient_server_error(code):
                state["last_error"] = ""
            else:
                state["last_error"] = code or "UNKNOWN"
            state["next_try"] = now + MATCH_ERROR_BACKOFF_S
            print("SERVER match failed {} code={}".format(_mac_bytes_to_hex(mac), code or "UNKNOWN"))

        checked += 1

    if calls == 0:
        match_rr_cursor = (start_idx + 1) % n
    return calls


# ===== MAIN LOOP =====
try:
    if DEBUG_ESPNOW:
        print(
            "ESPNOW cfg channel=", ESPNOW_CHANNEL,
            "peer_channel=", ESPNOW_PEER_CHANNEL,
            "mac=", bytes(my_mac).hex()
        )
    _initialize_server_client(time.monotonic())
    render_display()
    do_broadcast()

    phase = 0
    while True:
        now = time.monotonic()
        loop_started = now

        handled_events = _handle_button_inputs(now)
        debug_button_events_last = handled_events
        if handled_events > debug_button_events_max:
            debug_button_events_max = handled_events

        # Periodic broadcast
        if now - last_broadcast >= BROADCAST_INTERVAL:
            do_broadcast()

        # Receive (bounded)
        rx_this_tick = receive_all(RX_MAX_PACKETS_PER_TICK)
        debug_rx_last_per_tick = rx_this_tick
        if rx_this_tick > debug_rx_max_per_tick:
            debug_rx_max_per_tick = rx_this_tick

        # LEDs first so HTTP timing has less impact on perceived blink cadence.
        update_leds(phase)
        phase = (phase + 1) % 200

        _sync_local_gate_cache()
        network_ops = 0
        if network_ops < MAX_NETWORK_OPS_PER_TICK:
            if _sync_server_observations(now):
                network_ops += 1
        if network_ops < MAX_NETWORK_OPS_PER_TICK:
            network_ops += _sync_server_matches(now, max_calls=(MAX_NETWORK_OPS_PER_TICK - network_ops))
        debug_network_ops_last = network_ops

        # CHAT handshake timeout:
        # if peer never enters CHAT within 10s, return to SEARCH.
        if current_mode == MODE_CHAT and chat_wait_deadline > 0.0:
            if now >= chat_wait_deadline:
                peer = nearby_peers.get(chat_wait_peer_mac) if chat_wait_peer_mac else None
                if (
                    (not peer)
                    or (peer.get("mode") != MODE_CHAT)
                ):
                    set_mode(MODE_SEARCH)
                    continue
                chat_wait_deadline = 0.0

        if current_mode == MODE_CHAT and chat_peer_exit_deadline > 0.0:
            if now >= chat_peer_exit_deadline:
                set_mode(MODE_SEARCH)
                continue

        if DEBUG_ESPNOW and (now - last_debug_log >= 5.0):
            channel_text = "?"
            try:
                channel_text = str(wifi.radio.ap_info.channel)
            except Exception:
                pass
            loop_elapsed_ms = (time.monotonic() - loop_started) * 1000.0
            if loop_elapsed_ms > debug_loop_max_ms:
                debug_loop_max_ms = loop_elapsed_ms
            print(
                (
                    "DBG mode={} ch={} tx={} err={} rx={} parse_fail={} nearby={} "
                    "blocked_active={} srv_en={} auth_fail={} btn_evt={} btn_evt_max={} "
                    "rx_tick={} rx_tick_max={} net_ops={} srv_ms_last={:.1f} "
                    "srv_ms_max={:.1f} loop_ms_max={:.1f}"
                ).format(
                    MODE_NAMES[current_mode],
                    channel_text,
                    tx_attempts,
                    tx_errors,
                    rx_packets,
                    parse_failures,
                    len(nearby_peers),
                    len(auto_rematch_state),
                    int(server_enabled),
                    int(server_auth_failed),
                    debug_button_events_last,
                    debug_button_events_max,
                    debug_rx_last_per_tick,
                    debug_rx_max_per_tick,
                    debug_network_ops_last,
                    debug_server_call_last_ms,
                    debug_server_call_max_ms,
                    debug_loop_max_ms,
                )
            )
            last_debug_log = now

        # Refresh display (rate-limited)
        if display_dirty and (now - last_display_refresh >= DISPLAY_REFRESH):
            render_display()

        loop_elapsed_ms = (time.monotonic() - loop_started) * 1000.0
        if loop_elapsed_ms > debug_loop_max_ms:
            debug_loop_max_ms = loop_elapsed_ms
        time.sleep(LOOP_SLEEP_S)

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
