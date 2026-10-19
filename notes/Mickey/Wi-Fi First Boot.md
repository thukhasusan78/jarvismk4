# Wi-Fi First Boot

Provisioning is a soft access point by default. BluFi exists in Kconfig and is not enabled on mickey. The access-point name starts with Mickey.

A wrong Wi-Fi password needs a 60 second timeout or an NVS erase. `SystemReset` is unused, same as stock Otto. Boot still reconnects and closes the audio channel if Wi-Fi drops. See [[Wake Word and Boot Button]].
