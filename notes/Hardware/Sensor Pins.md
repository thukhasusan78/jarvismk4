# Sensor Pins

MPU6050: SDA GPIO 41, SCL GPIO 42, interrupt GPIO 40. TTP223 touch: GPIO 47. The sensor task samples both. IMU and touch MCP report `wired: true`. A light sensor is not started and still reports `wired: false`.

Pickup and fall use the motion sensor and cancel idle fidget. See [[Idle Director]] and [[Low Battery Inhibit]].
