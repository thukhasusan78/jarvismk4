# Cloudflare Tunnel

Jarvis binds `HOST=127.0.0.1` and `PORT=8000`. `VOICE_ALLOWED_ORIGINS` is https://jarvis.thukha.online. A cloudflared tunnel named jarvis-web is the only ingress. DNS for that name is proxied. Ports 80 and 443 on the VPS stay closed.

Cloudflare Access covers the whole hostname, including `/ws/voice`. The policy allows the owner's email only. The WebSocket sends the Access cookie because it is same-origin. Health is `GET /health` on localhost. See [[Jarvis HUD Address]] and [[Watchdog Soft Restart]].
