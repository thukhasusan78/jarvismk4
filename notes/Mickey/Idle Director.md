# Idle Director

The idle director is done, in `mickey_behavior.cc`. Face GIFs run in the idle state. Body motion waits until 60 seconds of inactivity, then a slow swing and an occasional reduced walk.

Wake word, pet, pickup, fall, a dashboard `self.otto.*` call, and deep sleep all cancel the fidget. Companion keepalive can hold `/xiaozhi/v1/` open during idle so the dashboard can dance the robot without a wake word. The fidget itself still runs if that socket is closed. See [[Idle Life Without the Cloud]] and [[Low Battery Inhibit]].
