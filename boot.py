import storage
import board
import digitalio

# Use Button A (D15) as our "Computer Override" switch
button = digitalio.DigitalInOut(board.D15)
button.direction = digitalio.Direction.INPUT
button.pull = digitalio.Pull.UP

# Logic:
# If Button A is pressed (value is False), the computer can write (readonly=True for MCU).
# If Button A is NOT pressed (value is True), the MagTag can write (readonly=False for MCU).
storage.remount("/", not button.value)
