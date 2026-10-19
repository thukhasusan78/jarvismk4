# Jarvis HUD Address

The voice HUD is served at https://jarvis.thukha.online. The path is Chrome or Edge, then Cloudflare Access, then a Cloudflare tunnel, then Jarvis bound to 127.0.0.1:8000.

`/ws/voice` is left unauthenticated in the app on purpose. The lock is Access, an origin allowlist of that hostname, and the localhost bind. See [[Cloudflare Tunnel]] and [[Voice HUD]].
