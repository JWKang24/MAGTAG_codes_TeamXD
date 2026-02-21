import time
import board
import digitalio
import storage
import supervisor

supervisor.runtime.autoreload = False

btn = digitalio.DigitalInOut(board.D15)  # Button A on MagTag
btn.direction = digitalio.Direction.INPUT
btn.pull = digitalio.Pull.UP

time.sleep(0.05)

if btn.value:  # Not pressed → lock USB drive
    storage.disable_usb_drive()
else:
    # Button held → allow USB drive
    pass