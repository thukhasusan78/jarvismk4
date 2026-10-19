#!/usr/bin/env python3
"""One-shot TCP snapshot. Prints state counts and exits. Does not poll."""

from __future__ import annotations

import subprocess
import sys
from collections import Counter

def main() -> int:
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr or "netstat failed\n")
        return completed.returncode or 1

    states: Counter[str] = Counter()
    local_4701 = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "TCP":
            continue
        state = parts[3] if parts[3] in {"LISTENING", "TIME_WAIT", "ESTABLISHED", "CLOSE_WAIT", "SYN_SENT", "FIN_WAIT_1", "FIN_WAIT_2", "LAST_ACK"} else parts[-2]
        states[state] += 1
        if ":4701" in parts[1] or ":4701" in parts[2]:
            local_4701.append(line.strip())

    print("TCP states:")
    for name, count in states.most_common():
        print(f"  {name}: {count}")
    print(f"TIME_WAIT total: {states['TIME_WAIT']}")
    print("Port 4701:")
    if local_4701:
        for line in local_4701:
            print(f"  {line}")
    else:
        print("  none")
    wide = [line for line in local_4701 if "0.0.0.0:4701" in line or "[::]:4701" in line]
    if wide:
        print("WARNING: 4701 is bound wider than 127.0.0.1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
