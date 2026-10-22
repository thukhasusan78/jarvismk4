# Mobile HUD Reconnect

A slow phone turn was often a half-open socket, not a slow model. There are three heartbeat layers, and they are easy to confuse:

- Server to page, WebSocket protocol level: the server pings every 20 seconds and closes the socket if no protocol pong arrives within 20 seconds (voice/server.py).
- Page to voice server, app level: the page sends {"type": "ping"} every 10 seconds; if no {"type": "pong"} arrives for 25 seconds, the page closes the socket and reconnects (viewer/index.html). There is no backoff ladder: retries are a flat 300 milliseconds via ensureVoice / scheduleVoiceRetry.
- Server to Gemini, upstream: the brain pings the Live session every 10 seconds with a 5 second pong timeout, plus a periodic silence-audio heartbeat, because Gemini aborts an idle Live socket after ~150 seconds (voice/gemini_live.py).

Text spoken while the socket is down is held and sent when it opens. Returning to the tab re-checks the socket (pageshow warms the voice connection); a socket silent past the 25 second pong rule is treated as dead. A screen wake lock is requested on boot. The status line shows CONNECTING, SECURE, or RECONNECTING. See [[Voice HUD]] and [[Burmese Voice Engine]].
