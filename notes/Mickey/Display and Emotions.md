# Display and Emotions

The panel is an ST7789-style SPI display. Expressions are GIFs through `Display::SetEmotion`. After UI setup the idle face is `staticstate`.

Backlight is GPIO 3. Chip-select is strapped to ground, so GPIO 12 is not a hand pin. Vision reactions change the face first, then a small body move. See [[Display Pins]] and [[Idle Director]].
