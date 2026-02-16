import time
import os
import board
import displayio
import wifi
import socketpool
from adafruit_httpserver import Server, Request, Response
from adafruit_miniqr import QRCode

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

# ----------------------------
# HTTP Server
# ----------------------------
pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, "/")

@server.route("/")
def index(request: Request):
    # Some versions don't have request.client / request.remote_addr, so don't rely on it
    print("GET /")
    html = "<html><body><h2>MagTag server running ✅</h2></body></html>"
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
while True:
    server.poll()
    time.sleep(0.01)