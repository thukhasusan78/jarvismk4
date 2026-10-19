# Build Command

The canonical build is `python scripts/build.py mickey --name mickey --language en-US` from the phoelone repo, after ESP-IDF v6.0.2 is installed and the target is esp32s3.

The first build downloads the component manager tree and can take more than 30 minutes on Windows. Linux is the faster path. Disconnect servo power for the first flash. Serial monitor is 115200 baud. See [[Mickey Board Identity]] and [[Do Not Remap Pins]].
