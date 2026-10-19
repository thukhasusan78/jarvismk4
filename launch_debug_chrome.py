#!/usr/bin/env python3
"""Launch the user's real Chrome with remote debugging so Jarvis can attach.

Quit every Chrome window first, including the system tray. Chrome ignores
--remote-debugging-port on an already-running instance; the flag applies
only to a fresh launch. Use this script instead of the normal Chrome icon
when Jarvis should attach.

This is not the dedicated agent profile (browser_profile/). The
--user-data-dir below is the real default Chrome user-data directory, so
the window keeps the logins already stored there.

Chrome 136 and later ignore --remote-debugging-port when that directory is
the default one. If nothing then listens on the port, attach_over_cdp
returns false with a reason.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from browser.session import configured_cdp_port
from build import ROOT


def chrome_user_data_dir() -> Path:
    """Real Chrome user-data directory. Never the agent profile."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            raise RuntimeError("LOCALAPPDATA is not set; cannot find the Chrome profile.")
        return Path(local) / "Google" / "Chrome" / "User Data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def chrome_executable() -> Path | None:
    if sys.platform == "win32":
        candidates = []
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env_name, "")
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
        candidates.append(Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    else:
        candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/chromium"),
        ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def chrome_command(executable: Path, user_data_dir: Path, port: int) -> list[str]:
    return [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
    ]


def _refuse_agent_profile(user_data: Path) -> None:
    agent = (ROOT / "browser_profile").resolve()
    resolved = user_data.resolve()
    if resolved == agent or agent in resolved.parents:
        raise RuntimeError(
            "Refusing to point Chrome at the agent profile. "
            "Attached mode uses the real Chrome user-data directory."
        )


def chrome_is_running() -> bool | None:
    """True when Chrome is open. None when the check itself could not run."""
    try:
        if sys.platform == "win32":
            completed = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return "chrome.exe" in completed.stdout.lower()
        if sys.platform == "darwin":
            names = ["Google Chrome"]
        else:
            names = ["chrome", "google-chrome"]
        for name in names:
            completed = subprocess.run(
                ["pgrep", "-x", name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if completed.returncode == 0:
                return True
        return False
    except (OSError, subprocess.TimeoutExpired):
        return None


def spawn_chrome(command: list[str]) -> None:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def main() -> int:
    port = configured_cdp_port()
    try:
        user_data = chrome_user_data_dir()
        _refuse_agent_profile(user_data)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    executable = chrome_executable()
    if executable is None:
        sys.stderr.write("Google Chrome was not found. Install Chrome, then run this script again.\n")
        return 1
    running = chrome_is_running()
    if running is None:
        sys.stderr.write(
            "Could not check whether Chrome is already running. "
            "Quit every Chrome window, then run this script again.\n"
        )
        return 1
    if running:
        sys.stderr.write(
            "Chrome is already running. Quit every Chrome window, including the system tray, "
            "then run this script again.\n"
            "Chrome ignores --remote-debugging-port unless this is a fresh launch.\n"
        )
        return 1
    command = chrome_command(executable, user_data, port)
    print(f"Starting Chrome with remote debugging on port {port}.")
    print(f"Profile: {user_data}")
    print("This is your real Chrome profile, not Jarvis's browser_profile.")
    print("Use this script instead of the normal Chrome icon while Jarvis needs to attach.")
    print(
        "Chrome 136 and later ignore remote debugging on the default user-data directory. "
        "If nothing listens on this port, that restriction is why."
    )
    spawn_chrome(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
