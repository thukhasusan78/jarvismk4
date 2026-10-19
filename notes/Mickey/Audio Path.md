# Audio Path

Audio is simplex I2S. The microphone and the speaker do not run full duplex on this board. While Mickey is speaking, microphone streaming stops unless a realtime echo-cancellation mode is on, and this SKU sets `audio_use_simplex` true.

Camera-board I2C on GPIO 15 and 16 is the speaker clock on Mickey. Those pins stay audio. See [[Microphone and Speaker Pins]] and [[Wake Word and Boot Button]].
