# Vision Interconnect Pins

The proposed link to a camera coprocessor uses four free GPIOs, and only after a bench check: GPIO 8 as interrupt input, GPIO 1 as UART receive, GPIO 2 as UART transmit, GPIO 13 as reset output. UART is 921600 8N1.

GPIO 45 and 48 stay unused. Photos are not on this link unless a capture was requested. A heartbeat timeout pulses reset. After three failed recovers, vision is marked unwired and the main robot keeps talking. See [[Vision Coprocessor Decision]] and [[Do Not Remap Pins]].
