# Microphone and Speaker Pins

Microphone I2S: WS GPIO 4, SCK GPIO 5, data GPIO 6. Speaker amplifier I2S: data GPIO 7, bit clock GPIO 15, word select GPIO 16.

GPIO 15 and 16 are not free for a camera. Simplex audio means capture and playback do not overlap on this board. See [[Audio Path]] and [[Frozen GPIO Map]].
