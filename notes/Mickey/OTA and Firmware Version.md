# OTA and Firmware Version

Full download, flash, and reboot machinery exists. The lab server has advertised a dummy version `0.0.0`, so upgrade is skipped until a real version is published. Stock activation UI (a spoken digit code) runs only if the OTA JSON includes `activation`. The lab VPS omits it.

Board `config.json` type and name are `mickey`. Kconfig symbol is `BOARD_TYPE_MICKEY`. See [[Phoelone OTA Address]] and [[Mickey Board Identity]].
