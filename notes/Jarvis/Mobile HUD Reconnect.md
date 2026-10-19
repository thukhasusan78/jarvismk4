# Mobile HUD Reconnect

A slow phone turn was often a half-open socket, not a slow model. The server ping is every 20 seconds. If the page hears nothing for 45 seconds, it closes and reconnects. Backoff starts at 500 milliseconds and stops at 3 seconds.

Text spoken while the socket is down is held and sent when it opens. Returning to the tab after 30 seconds of silence treats the socket as dead. A screen wake lock is requested on boot. The status line shows CONNECTING, SECURE, or RECONNECTING. See [[Voice HUD]] and [[Burmese Voice Engine]].
