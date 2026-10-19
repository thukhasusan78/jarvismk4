# Servo Pins

Four servos, frozen: right leg GPIO 39, right foot GPIO 38, left leg GPIO 17, left foot GPIO 18. Hands are not wired.

Servos need their own power rail and a shared ground. They must not be powered from an ESP32-S3 pin or a weak 3.3 volt rail. First flash is done with servo power disconnected. See [[No Hands Four Servos]] and [[Frozen GPIO Map]].
