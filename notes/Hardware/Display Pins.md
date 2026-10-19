# Display Pins

Display SPI: clock GPIO 9, data GPIO 10, reset GPIO 11, data/command GPIO 46. Backlight is GPIO 3. Chip-select is tied to ground, not a GPIO. GPIO 12 stays the LCD chip-select net only and is not a servo.

Do not move the backlight to free a camera clock. The vision plan keeps this panel on the main chip. See [[Display and Emotions]] and [[Vision Coprocessor Decision]].
