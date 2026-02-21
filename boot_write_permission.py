import time
import board
import digitalio
import storage

# Use front Button A (D15)
button = digitalio.DigitalInOut(board.D15)
button.switch_to_input(pull=digitalio.Pull.UP)

time.sleep(0.5)

# Button pressed (LOW) -> readonly=False, so CircuitPython can write settings.toml.
storage.remount("/", readonly=button.value)