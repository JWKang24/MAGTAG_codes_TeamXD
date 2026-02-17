# MagTag E-Ink Dashboard -- Daily Wisdom Display
# Pure ASCII file -- no special characters anywhere
import supervisor
supervisor.runtime.autoreload = False

import time
import board
import displayio
import terminalio
import random
import neopixel
import digitalio
from adafruit_display_text import label

# -- NeoPixel setup --
pixels = neopixel.NeoPixel(board.NEOPIXEL, 4, brightness=0.05)
pixels.fill((255, 0, 0))

# -- Button setup (MagTag has 4 buttons: D15, D14, D12, D11) --
button_pins = (board.D15, board.D14, board.D12, board.D11)
buttons = []
for pin in button_pins:
    b = digitalio.DigitalInOut(pin)
    b.direction = digitalio.Direction.INPUT
    b.pull = digitalio.Pull.UP
    buttons.append(b)

# -- Quotes --
QUOTES = [
    "The only way to do great\nwork is to love what\nyou do.  -- Steve Jobs",
    "In the middle of\ndifficulty lies\nopportunity. -- Einstein",
    "Imagination is more\nimportant than knowledge.\n  -- Albert Einstein",
    "Stay hungry,\nstay foolish.\n  -- Steve Jobs",
    "Not all those who\nwander are lost.\n  -- J.R.R. Tolkien",
    "The best time to plant\na tree was 20 years ago.\nThe second best is now.",
    "Do or do not.\nThere is no try.\n  -- Yoda",
    "Be yourself; everyone\nelse is already taken.\n  -- Oscar Wilde",
]

ROTATE_SECONDS = 300  # auto-rotate every 5 minutes

# -- Helper: show error on screen --
def show_error(msg):
    try:
        d = board.DISPLAY
        g = displayio.Group()
        bg = displayio.Bitmap(296, 128, 1)
        pal = displayio.Palette(1)
        pal[0] = 0xFFFFFF
        g.append(displayio.TileGrid(bg, pixel_shader=pal))
        err = label.Label(
            terminalio.FONT,
            text="ERROR:\n" + str(msg)[:200],
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(4, 4),
            scale=1,
            line_spacing=1.2,
        )
        g.append(err)
        d.root_group = g
        while d.time_to_refresh > 0:
            time.sleep(0.5)
        d.refresh()
    except Exception:
        pass


# -- Build and refresh a quote screen --
def show_quote(index):
    display = board.DISPLAY

    main = displayio.Group()

    # White background
    bg_bmp = displayio.Bitmap(296, 128, 1)
    bg_pal = displayio.Palette(1)
    bg_pal[0] = 0xFFFFFF
    main.append(displayio.TileGrid(bg_bmp, pixel_shader=bg_pal))

    # Black palette
    black_pal = displayio.Palette(1)
    black_pal[0] = 0x000000

    # Top bar
    top_bar = displayio.Bitmap(296, 4, 1)
    main.append(displayio.TileGrid(top_bar, pixel_shader=black_pal, x=0, y=26))

    # Bottom bar
    bot_bar = displayio.Bitmap(296, 2, 1)
    main.append(displayio.TileGrid(bot_bar, pixel_shader=black_pal, x=0, y=106))

    # Side accent strip
    gray_pal = displayio.Palette(1)
    gray_pal[0] = 0x999999
    side_strip = displayio.Bitmap(5, 72, 1)
    main.append(displayio.TileGrid(side_strip, pixel_shader=gray_pal, x=10, y=33))

    # Dots along left margin
    for dy in (0, 18, 36, 54, 72):
        dot = displayio.Bitmap(3, 3, 1)
        main.append(displayio.TileGrid(dot, pixel_shader=black_pal, x=11, y=33 + dy))

    # Title
    title = label.Label(
        terminalio.FONT,
        text="== DAILY WISDOM ==",
        color=0x000000,
        anchor_point=(0.5, 0.0),
        anchored_position=(148, 6),
        scale=2,
    )
    main.append(title)

    # Quote
    quote = label.Label(
        terminalio.FONT,
        text=QUOTES[index],
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(22, 38),
        scale=1,
        line_spacing=1.25,
    )
    main.append(quote)

    # Footer with quote number
    num_text = "< " + str(index + 1) + "/" + str(len(QUOTES)) + " >"
    footer = label.Label(
        terminalio.FONT,
        text=num_text,
        color=0x999999,
        anchor_point=(0.5, 0.0),
        anchored_position=(148, 112),
        scale=1,
    )
    main.append(footer)

    # Push to display
    display.root_group = main
    while display.time_to_refresh > 0:
        time.sleep(0.5)
    display.refresh()


# -- Main code --
try:
    pixels.fill((255, 180, 0))
    quote_index = random.randint(0, len(QUOTES) - 1)
    show_quote(quote_index)
    pixels.fill((0, 255, 0))
    time.sleep(0.5)
    pixels.fill(0)

    last_rotate = time.monotonic()

    while True:
        now = time.monotonic()

        # Auto-rotate every ROTATE_SECONDS
        if now - last_rotate >= ROTATE_SECONDS:
            quote_index = (quote_index + 1) % len(QUOTES)
            pixels.fill((0, 0, 80))
            show_quote(quote_index)
            pixels.fill(0)
            last_rotate = now

        # Button A (left) = previous quote
        if not buttons[0].value:
            quote_index = (quote_index - 1) % len(QUOTES)
            pixels.fill((80, 0, 80))
            show_quote(quote_index)
            pixels.fill(0)
            last_rotate = now
            while not buttons[0].value:
                time.sleep(0.05)

        # Button B = random quote
        if not buttons[1].value:
            quote_index = random.randint(0, len(QUOTES) - 1)
            pixels.fill((0, 80, 80))
            show_quote(quote_index)
            pixels.fill(0)
            last_rotate = now
            while not buttons[1].value:
                time.sleep(0.05)

        # Button C = next quote
        if not buttons[2].value:
            quote_index = (quote_index + 1) % len(QUOTES)
            pixels.fill((80, 0, 80))
            show_quote(quote_index)
            pixels.fill(0)
            last_rotate = now
            while not buttons[2].value:
                time.sleep(0.05)

        # Button D (right) = toggle NeoPixel nightlight
        if not buttons[3].value:
            pixels.fill((80, 60, 30))
            time.sleep(2.0)
            pixels.fill(0)
            while not buttons[3].value:
                time.sleep(0.05)

        time.sleep(0.1)

except Exception as e:
    for i in range(10):
        pixels.fill((255, 0, 0))
        time.sleep(0.15)
        pixels.fill(0)
        time.sleep(0.15)
    show_error(e)
