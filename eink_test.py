# E-Ink Image Test for Adafruit MagTag
# Displays ./images/Georgia.bmp full screen

import time
import board
import displayio
from adafruit_display_text import label
import terminalio

# Prevent auto reload while testing
import supervisor
supervisor.runtime.autoreload = False

display = board.DISPLAY

# Release any previous displays
displayio.release_displays()

try:
    # Load BMP file
    bmp = displayio.OnDiskBitmap("/images/Georgia.bmp")
    tile_grid = displayio.TileGrid(
        bmp,
        pixel_shader=bmp.pixel_shader
    )

    group = displayio.Group()
    group.append(tile_grid)

    display.root_group = group

    # Wait until display is ready
    while display.time_to_refresh > 0:
        time.sleep(0.1)

    display.refresh()

except Exception as e:
    # If image fails to load, show error text
    group = displayio.Group()

    bg = displayio.Bitmap(296, 128, 1)
    palette = displayio.Palette(1)
    palette[0] = 0xFFFFFF
    group.append(displayio.TileGrid(bg, pixel_shader=palette))

    error_label = label.Label(
        terminalio.FONT,
        text="ERROR:\n" + str(e),
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(4, 4),
        scale=1,
        line_spacing=1.2,
    )

    group.append(error_label)
    display.root_group = group

    while display.time_to_refresh > 0:
        time.sleep(0.1)

    display.refresh()

# Do nothing afterward (E-Ink is static)
while True:
    time.sleep(1)