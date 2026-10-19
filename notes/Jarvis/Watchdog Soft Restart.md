# Watchdog Soft Restart

`watchdog.py` checks that the process is listening, then soft-restarts if it is not. It must not run git fetch, git reset, or git clean. A hard reset on 13 August 2026 wiped uncommitted work.

If the soft restart fails, the watchdog sends a fatal Telegram note and waits. The git tree is left alone. See [[Cloudflare Tunnel]] and [[Memory and Tasks]].
