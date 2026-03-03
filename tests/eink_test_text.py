# SPDX-FileCopyrightText: 2021 Carter Nelson for Adafruit Industries
#
# SPDX-License-Identifier: MIT

import time
import board
import alarm
import displayio
import os
import adafruit_imageload
import terminalio
from adafruit_display_text import label

# get the display
epd = board.DISPLAY
epd.rotation = 270

# choose two wake buttons
buttons = (board.BUTTON_A, board.BUTTON_B)
pin_alarms = [alarm.pin.PinAlarm(pin=pin, value=False, pull=True) for pin in buttons]

# list BMP files in /images (sorted for consistent order)
try:
    files = [f for f in os.listdir("/images") if f.lower().endswith(".bmp")]
    files.sort()
except Exception:
    files = []

# if no images, show nothing and sleep
if not files:
    alarm.exit_and_deep_sleep_until_alarms(*pin_alarms)

# persistent index in sleep memory
# sleep_memory is a bytearray; use 2 bytes to store index up to 65535 images
idx = alarm.sleep_memory[0] | (alarm.sleep_memory[1] << 8)
idx %= len(files)

# detect which button woke us (if available)
direction = 1  # default: next
try:
    wa = alarm.wake_alarm
    if isinstance(wa, alarm.pin.PinAlarm):
        if wa.pin == board.BUTTON_B:
            direction = -1
except Exception:
    pass

# advance index based on wake source
idx = (idx + direction) % len(files)

# store index back
alarm.sleep_memory[0] = idx & 0xFF
alarm.sleep_memory[1] = (idx >> 8) & 0xFF

# show bitmap
bmp_file = "/images/" + files[idx]
bitmap, palette = adafruit_imageload.load(
    bmp_file,
    bitmap=displayio.Bitmap,
    palette=displayio.Palette,
)
bg_bitmap = displayio.Bitmap(296, 128, 1)
bg_palette = displayio.Palette(1)
bg_palette[0] = 0xFFFFFF

bg_tile_grid = displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette)
tile_grid = displayio.TileGrid(bitmap, pixel_shader=palette, x=0, y=0)
text_area = label.Label(
    terminalio.FONT,
    text="Hello World!",
    color=0x000000,
    anchor_point=(0.0, 0.5),
    anchored_position=(160, 64),
    scale=2,
)

group = displayio.Group()
group.append(bg_tile_grid)
group.append(tile_grid)
group.append(text_area)
epd.root_group = group

time.sleep(epd.time_to_refresh + 0.01)
epd.refresh()
while epd.busy:
    pass

# go to sleep until next button press
alarm.exit_and_deep_sleep_until_alarms(*pin_alarms)
