import time
import board
import displayio
import terminalio
import neopixel
import adafruit_imageload
from adafruit_display_text import label

# Demo configuration
IMAGE_FILE = "/images/Yarn_ball_needles.bmp"
DISPLAY_TEXT = "You both enjoy fiber arts!"
TEXT_COLOR = 0x000000
TEXT_POSITION = (153, 22)
TEXT_SCALE = 2
TEXT_WRAP_CHARS = 11
TEXT_LINE_SPACING = 1.2
NEOPIXEL_COLOR = (255, 60, 0)
NEOPIXEL_BRIGHTNESS = 0.15

# Display setup
epd = board.DISPLAY
epd.rotation = 270

# NeoPixel setup
pixels = neopixel.NeoPixel(board.NEOPIXEL, 4, brightness=NEOPIXEL_BRIGHTNESS)
pixels.fill(NEOPIXEL_COLOR)
pixels.show()

SCREEN_WIDTH = 296
SCREEN_HEIGHT = 128
LEFT_PANEL_WIDTH = SCREEN_WIDTH // 2
RIGHT_PANEL_X = LEFT_PANEL_WIDTH + 12


def load_bitmap(path):
    try:
        return adafruit_imageload.load(
            path,
            bitmap=displayio.Bitmap,
            palette=displayio.Palette,
        ) + (None,)
    except Exception as ex:
        return None, None, str(ex)


def wrap_text(text, max_chars):
    if not text:
        return ""

    lines = []
    for raw_line in text.split("\n"):
        words = raw_line.split()
        if not words:
            lines.append("")
            continue

        current = words[0]
        for word in words[1:]:
            if len(current) + 1 + len(word) <= max_chars:
                current += " " + word
            else:
                lines.append(current)
                current = word
        lines.append(current)

    return "\n".join(lines)


def build_screen():
    group = displayio.Group()

    bg_bitmap = displayio.Bitmap(SCREEN_WIDTH, SCREEN_HEIGHT, 1)
    bg_palette = displayio.Palette(1)
    bg_palette[0] = 0xFFFFFF
    group.append(displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette))

    bitmap, palette, image_error = load_bitmap(IMAGE_FILE)
    if bitmap is not None and palette is not None:
        image_x = max(0, (LEFT_PANEL_WIDTH - bitmap.width) // 2)
        image_y = max(0, (SCREEN_HEIGHT - bitmap.height) // 2)
        group.append(
            displayio.TileGrid(bitmap, pixel_shader=palette, x=image_x, y=image_y)
        )
    else:
        group.append(
            label.Label(
                terminalio.FONT,
                text="Image\nload\nfailed",
                color=0x000000,
                anchor_point=(0.5, 0.5),
                anchored_position=(LEFT_PANEL_WIDTH // 2, SCREEN_HEIGHT // 2 - 10),
                scale=2,
                line_spacing=1.1,
            )
        )
        if image_error:
            group.append(
                label.Label(
                    terminalio.FONT,
                    text=wrap_text(image_error, 16)[:64],
                    color=0x000000,
                    anchor_point=(0.0, 0.0),
                    anchored_position=(6, 92),
                    scale=1,
                    line_spacing=1.0,
                )
            )

    text_area = label.Label(
        terminalio.FONT,
        text=wrap_text(DISPLAY_TEXT, TEXT_WRAP_CHARS),
        color=TEXT_COLOR,
        anchor_point=(0.0, 0.0),
        anchored_position=(RIGHT_PANEL_X, TEXT_POSITION[1]),
        scale=TEXT_SCALE,
        line_spacing=TEXT_LINE_SPACING,
    )
    group.append(text_area)

    return group


epd.root_group = build_screen()

time.sleep(epd.time_to_refresh + 0.01)
epd.refresh()
while epd.busy:
    pass

while True:
    time.sleep(1)
