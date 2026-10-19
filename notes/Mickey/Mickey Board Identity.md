# Mickey Board Identity

The firmware profile is the `mickey` board under `main/boards/mickey`, chip ESP32-S3, module N16R8: 16 MB flash and 8 MB octal PSRAM. Preferred SDK is ESP-IDF v6.0.2. PlatformIO is not used.

OTA identity is `mickey`. Do not ship this robot as `otto-robot`. SoftAP and hostname prefix are Mickey. Firmware language for this profile is English (`en-US`); Myanmar is not one of the built-in interface locales. See [[Build Command]] and [[Phoelone OTA Address]].
