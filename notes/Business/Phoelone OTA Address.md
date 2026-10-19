# Phoelone OTA Address

Production OTA is on. `CONFIG_OTA_URL` is https://phoelone.thukha.online/xiaozhi/ota/. On boot the device POSTs, then stores `websocket.url` and the token in NVS.

The board type in that POST is `mickey`, not `otto-robot`. A lab server that advertises firmware `0.0.0` makes the upgrade step skip. See [[OTA and Firmware Version]] and [[Public Hosts]].
