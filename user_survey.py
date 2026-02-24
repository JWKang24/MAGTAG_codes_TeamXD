import time
import os
import board
import displayio
import wifi
import socketpool
from adafruit_httpserver import Server, Request, Response, GET, POST
from adafruit_miniqr import QRCode

# ----------------------------
# Survey helpers
# ----------------------------
MAX_INTERESTS = 5


def parse_csv(text):
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def html_escape(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def interest_label(text):
    label = (text or "").replace("_", " ").strip().lower()
    if not label:
        return ""
    words = [w for w in label.split(" ") if w]
    return " ".join(w[0].upper() + w[1:] for w in words)


def build_interest_lookup(interests):
    lookup = {}
    for raw in interests:
        base = (raw or "").strip()
        if not base:
            continue
        variants = (
            base,
            base.lower(),
            base.replace("_", " "),
            base.lower().replace("_", " "),
            base.replace("-", " "),
            base.lower().replace("-", " "),
        )
        for item in variants:
            key = item.strip().lower()
            if key and key not in lookup:
                lookup[key] = base
    return lookup


def toml_escape(text):
    return (text or "").replace("\\", "\\\\").replace('"', '\\"')


def url_decode(text):
    text = (text or "").replace("+", " ")
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "%" and i + 2 < len(text):
            try:
                out.append(chr(int(text[i + 1:i + 3], 16)))
                i += 3
                continue
            except Exception:
                pass
        out.append(c)
        i += 1
    return "".join(out)


def parse_form_urlencoded(body):
    data = {}
    if not body:
        return data
    for pair in body.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        key = url_decode(k)
        val = url_decode(v)
        if key in data:
            old = data[key]
            if isinstance(old, list):
                old.append(val)
            else:
                data[key] = [old, val]
        else:
            data[key] = val
    return data


def get_request_form_data(request):
    # Try parsed form first if available in this httpserver version.
    try:
        if hasattr(request, "form_data"):
            fd = request.form_data
            if callable(fd):
                fd = fd()
            if isinstance(fd, dict):
                return fd
    except Exception:
        pass

    raw = b""
    for attr in ("body", "raw_request", "_body"):
        try:
            if hasattr(request, attr):
                value = getattr(request, attr)
                if callable(value):
                    value = value()
                if isinstance(value, bytes):
                    raw = value
                    break
                if isinstance(value, str):
                    raw = value.encode("utf-8")
                    break
        except Exception:
            pass

    try:
        text = raw.decode("utf-8")
    except Exception:
        text = ""
    return parse_form_urlencoded(text)


def get_settings_path():
    try:
        os.stat("/settings.toml")
        return "/settings.toml"
    except Exception:
        return "settings.toml"


def load_interest_options():
    options = []
    try:
        for name in os.listdir("/images"):
            lower = name.lower()
            if not lower.endswith(".bmp"):
                continue
            interest = name[:-4].strip()
            if interest and interest not in options:
                options.append(interest)
    except Exception:
        pass
    options.sort(key=lambda s: s.lower())
    return options


def write_settings(name, interests):
    path = get_settings_path()
    clean_name = (name or "MagTag").strip()[:20]
    clean_interests = [x.strip() for x in interests if x and x.strip()][:MAX_INTERESTS]
    interests_csv = ", ".join(clean_interests)

    with open(path, "r") as f:
        lines = f.read().splitlines()

    has_name = False
    has_interests = False
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("MY_NAME"):
            out.append('MY_NAME= "{}"'.format(toml_escape(clean_name)))
            has_name = True
        elif stripped.startswith("MY_INTERESTS"):
            out.append('MY_INTERESTS= "{}"'.format(toml_escape(interests_csv)))
            has_interests = True
        else:
            out.append(line)

    if not has_name:
        out.append('MY_NAME= "{}"'.format(toml_escape(clean_name)))
    if not has_interests:
        out.append('MY_INTERESTS= "{}"'.format(toml_escape(interests_csv)))

    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")

    return clean_name, clean_interests


def build_form_page(name, current_hobbies, message=""):
    msg_html = ""
    if message:
        msg_html = '<p style="color:#0a7a2f;"><b>{}</b></p>'.format(html_escape(message))

    html = """
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Badge Setup</title>
</head>
<body style="font-family: sans-serif; max-width: 560px; margin: 20px auto; line-height: 1.4;">
<h2>Badge Setup</h2>
{}
<form method="POST" action="/" onsubmit="return enforceMax();">
<label><b>Name</b></label><br>
<input name="name" value="{}" style="width:100%; padding:8px; margin:6px 0 12px 0;"><br>
<label><b>Choose up to {} interests</b></label><br>
""".format(msg_html, html_escape(name), MAX_INTERESTS)

    for interest in ALL_INTERESTS:
        checked = "checked" if interest in current_hobbies else ""
        display_label = interest_label(interest)
        html += """
<label>
  <input type="checkbox" name="badge" value="{}" {}>
  {}
</label><br>
""".format(html_escape(interest), checked, html_escape(display_label))

    html += """
<br>
<input type="submit" value="Save" style="padding:10px 14px;">
</form>
<script>
function enforceMax() {
  const boxes = document.querySelectorAll('input[name="badge"]:checked');
  if (boxes.length > """ + str(MAX_INTERESTS) + """) {
    alert("Please choose up to """ + str(MAX_INTERESTS) + """ interests.");
    return false;
  }
  return true;
}
</script>
</body>
</html>
"""
    return html


# ----------------------------
# WiFi
# ----------------------------
SSID = os.getenv("CIRCUITPY_WIFI_SSID")
PW = os.getenv("CIRCUITPY_WIFI_PASSWORD")
PORT = int(os.getenv("CIRCUITPY_WEB_PORT") or "80")

if not SSID or not PW:
    raise RuntimeError("Missing CIRCUITPY_WIFI_SSID/PASSWORD in settings.toml")

print("Connecting to Wi-Fi...")
wifi.radio.connect(SSID, PW)

ip_str = str(wifi.radio.ipv4_address)
url = f"http://{ip_str}:{PORT}/" if PORT != 80 else f"http://{ip_str}/"
print("Connected:", url)

ALL_INTERESTS = load_interest_options()
if not ALL_INTERESTS:
    ALL_INTERESTS = ["python", "circuitpython", "electronics"]

current_name = (os.getenv("MY_NAME") or "MagTag").strip()
current_hobbies = parse_csv(os.getenv("MY_INTERESTS") or "")
survey_complete = False

# ----------------------------
# HTTP Server
# ----------------------------
pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, "/")


@server.route("/", [GET, POST])
def index(request: Request):
    global current_name, current_hobbies, survey_complete

    method = "GET"
    try:
        method = request.method
    except Exception:
        pass

    if method == "POST":
        print("POST /")
        form = get_request_form_data(request)
        name = form.get("name", current_name)
        selected = form.get("badge", [])
        if not isinstance(selected, list):
            selected = [selected] if selected else []

        allowed_lookup = build_interest_lookup(ALL_INTERESTS)
        filtered = []
        for x in selected:
            key = (x or "").strip().lower()
            canonical = allowed_lookup.get(key)
            if canonical and canonical not in filtered:
                filtered.append(canonical)
        filtered = filtered[:MAX_INTERESTS]

        try:
            current_name, current_hobbies = write_settings(name, filtered)
            survey_complete = True
            msg = "Saved. Starting nearby user search..."
        except Exception as ex:
            msg = "Save failed: {}".format(ex)

        html = build_form_page(current_name, current_hobbies, msg)
        return Response(request, html, content_type="text/html")

    print("GET /")
    html = build_form_page(current_name, current_hobbies)
    return Response(request, html, content_type="text/html")


server.start(ip_str, PORT)
print("Server started on", ip_str, "port", PORT)


# ----------------------------
# QR code bitmap
# ----------------------------
def make_qr_bitmap(text, scale=2, border=2):
    qr = QRCode()
    qr.add_data(text)
    qr.make()

    m = qr.matrix
    w = m.width
    h = m.height

    bmp_w = (w + 2 * border) * scale
    bmp_h = (h + 2 * border) * scale
    bmp = displayio.Bitmap(bmp_w, bmp_h, 2)

    pal = displayio.Palette(2)
    pal[0] = 0xFFFFFF
    pal[1] = 0x000000

    for y in range(h):
        for x in range(w):
            if m[x, y]:
                x0 = (x + border) * scale
                y0 = (y + border) * scale
                for yy in range(y0, y0 + scale):
                    for xx in range(x0, x0 + scale):
                        bmp[xx, yy] = 1

    return bmp, pal


# ----------------------------
# Display QR
# ----------------------------
epd = board.DISPLAY
epd.rotation = 270

qr_bmp, qr_pal = make_qr_bitmap(url, scale=2, border=2)

g = displayio.Group()

bg = displayio.Bitmap(296, 128, 1)
bgpal = displayio.Palette(1)
bgpal[0] = 0xFFFFFF
g.append(displayio.TileGrid(bg, pixel_shader=bgpal))

x = (296 - qr_bmp.width) // 2
y = (128 - qr_bmp.height) // 2
g.append(displayio.TileGrid(qr_bmp, pixel_shader=qr_pal, x=x, y=y))

epd.root_group = g
time.sleep(epd.time_to_refresh + 0.01)
epd.refresh()
while epd.busy:
    pass

print("QR displayed. Scan:", url)

# ----------------------------
# Main loop
# ----------------------------
while not survey_complete:
    server.poll()
    time.sleep(0.01)
