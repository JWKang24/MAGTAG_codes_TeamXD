import time
import os
import board
import displayio
import wifi
import socketpool
from adafruit_httpserver import Server, Request, Response, GET, POST
from adafruit_miniqr import QRCode

import server_match_client


MAX_NAME_LEN = 20
MAX_INTEREST_LEN = 8000


def html_escape(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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
                out.append(chr(int(text[i + 1 : i + 3], 16)))
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


def write_settings(name, interest_blurb):
    path = get_settings_path()
    clean_name = (name or "MagTag").strip()[:MAX_NAME_LEN]
    clean_interest = (interest_blurb or "").strip()[:MAX_INTEREST_LEN]

    with open(path, "r") as f:
        lines = f.read().splitlines()

    replacements = {
        "MY_NAME": clean_name,
        "MY_INTERESTS": clean_interest,
    }
    seen = {k: False for k in replacements}
    out = []

    for line in lines:
        stripped = line.strip()
        replaced = False
        for key, value in replacements.items():
            if stripped.startswith(key):
                out.append('{}= "{}"'.format(key, toml_escape(value)))
                seen[key] = True
                replaced = True
                break
        if not replaced:
            out.append(line)

    for key, value in replacements.items():
        if not seen[key]:
            out.append('{}= "{}"'.format(key, toml_escape(value)))

    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")

    return clean_name, clean_interest


def build_form_page(
    interest_blurb,
    device_id,
    message="",
):
    msg_html = ""
    if message:
        msg_html = '<p style="color:#0a7a2f;"><b>{}</b></p>'.format(html_escape(message))

    return """
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Badge Setup</title>
</head>
<body style="font-family: sans-serif; max-width: 640px; margin: 20px auto; line-height: 1.4;">
<h2>Badge Setup</h2>
{}
<p><b>Device ID:</b> {}</p>
<form method="POST" action="/">
<label><b>Name</b></label><br>
<input name="name" value="" style="width:100%; padding:8px; margin:6px 0 12px 0;"><br>

<label><b>Interest Paragraph</b></label><br>
<textarea name="interest_blurb" rows="5" style="width:100%; padding:8px; margin:6px 0 12px 0;">{}</textarea><br>

<input type="submit" value="Save and Continue" style="padding:10px 14px;">
</form>
</body>
</html>
""".format(
        msg_html,
        html_escape(device_id),
        html_escape(interest_blurb),
    )


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
url = "http://{}:{}/".format(ip_str, PORT) if PORT != 80 else "http://{}/".format(ip_str)
print("Connected:", url)

device_id = server_match_client.make_device_id(wifi.radio.mac_address)

current_name = (os.getenv("MY_NAME") or "MagTag").strip()
current_server_base_url = (os.getenv("MATCH_SERVER_BASE_URL") or "").strip()
current_server_app_key = (os.getenv("MATCH_SERVER_APP_KEY") or "").strip()
current_interest_blurb = ""
survey_complete = False

# ----------------------------
# HTTP Server
# ----------------------------
pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, "/")


@server.route("/", [GET, POST])
def index(request: Request):
    global current_name, current_server_base_url, current_server_app_key
    global current_interest_blurb, survey_complete

    method = "GET"
    try:
        method = request.method
    except Exception:
        pass

    if method == "POST":
        print("POST /")
        form = get_request_form_data(request)

        name = form.get("name", "")
        interest_blurb = (form.get("interest_blurb", "") or "").strip()[:MAX_INTEREST_LEN]

        try:
            current_name, current_interest_blurb = write_settings(name, interest_blurb)
        except Exception as ex:
            html = build_form_page(
                interest_blurb,
                device_id,
                "Save failed: {}".format(ex),
            )
            return Response(request, html, content_type="text/html")

        upload_note = ""
        if interest_blurb:
            if current_server_base_url and current_server_app_key:
                try:
                    client = server_match_client.ServerMatchClient(
                        base_url=current_server_base_url,
                        app_key=current_server_app_key,
                        timeout_s=3.0,
                    )
                    result = client.put_interest(device_id, interest_blurb)
                    if result.get("ok"):
                        upload_note = "Interest uploaded to server."
                    else:
                        err_code = result.get("error_code") or "UNKNOWN"
                        upload_note = "Interest upload failed: {}".format(
                            err_code
                        )
                except Exception as ex:
                    upload_note = "Interest upload failed: {}".format(ex)
            else:
                upload_note = "Interest not uploaded: server config missing."

        message = "Saved. Starting nearby user search..."
        if upload_note:
            message = "{} {}".format(message, upload_note)

        survey_complete = True
        html = build_form_page(
            "",
            device_id,
            message,
        )
        return Response(request, html, content_type="text/html")

    print("GET /")
    html = build_form_page(
        "",
        device_id,
    )
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
