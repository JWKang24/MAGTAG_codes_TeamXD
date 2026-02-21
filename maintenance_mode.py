import time
import board
import digitalio

# Use same pin as maintenance button
_maint_btn = digitalio.DigitalInOut(board.D15)
_maint_btn.direction = digitalio.Direction.INPUT
_maint_btn.pull = digitalio.Pull.UP

time.sleep(0.05)

if not _maint_btn.value:  # button held
    print("Maintenance mode: halting code.py")
    while True:
        time.sleep(1)

# IMPORTANT: release the pin so the rest of your code can use it
_maint_btn.deinit()