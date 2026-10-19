# Vision Coprocessor Decision

The main ESP32-S3 has no pins and no spare CPU for a camera. Mic, speaker, display, four servos, the motion sensor, and the touch key already occupy it. A DVP camera needs about eleven pins, and the frozen map forbids a remap.

The decision is a second chip that owns the camera and on-device models. The main board only receives tiny events and, when asked, one JPEG. Default module is another ESP32-S3 with a camera, such as XIAO ESP32-S3 Sense, with Wi-Fi off. Himax WiseEye2 is the low-power alternative and is weaker at five-person face ID. ESP32-P4 is a later upgrade, not required for reflexes. See [[Vision Interconnect Pins]] and [[Face ID Five Slots]].
