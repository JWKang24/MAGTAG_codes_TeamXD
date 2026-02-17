storage.remount("/", not button.value)
import storage
import supervisor
import board
import digitalio

# Setup a physical override button (Button A)
button = digitalio.DigitalInOut(board.D15)
button.direction = digitalio.Direction.INPUT
button.pull = digitalio.Pull.UP

# If Button A is held OR a laptop is detected, laptop gets write access.
if not button.value or supervisor.runtime.serial_connected:
    storage.remount("/", True)
else:
    storage.remount("/", False)
