# Do Not Remap Pins

GOAL.md and the August production plan both say the assembled wiring already matches `config.h`. New modules are added on free pins, or on the coprocessor, not by moving audio, display, or servo pins.

Confirm GPIO 1, 2, 8, and 13 on the bench before soldering a vision board. Do not invent replacements. A build that compiles is not a hardware test. See [[Frozen GPIO Map]] and [[Build Command]].
