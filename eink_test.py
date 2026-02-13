# SPDX-FileCopyrightText: 2021 Carter Nelson for Adafruit Industries
#
# SPDX-License-Identifier: MIT

import time
import board
import alarm
import displayio
import os

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
bitmap = displayio.OnDiskBitmap(bmp_file)
tile_grid = displayio.TileGrid(bitmap, pixel_shader=bitmap.pixel_shader)

group = displayio.Group()
group.append(tile_grid)
epd.root_group = group

time.sleep(epd.time_to_refresh + 0.01)
epd.refresh()
while epd.busy:
    pass

# go to sleep until next button press
alarm.exit_and_deep_sleep_until_alarms(*pin_alarms)