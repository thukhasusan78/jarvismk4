const GRAPH = {
  "nodes": [
    {
      "id": 0,
      "label": "Jarvis Addresses the Boss",
      "group": "Biography",
      "excerpt": "# Jarvis Addresses the Boss\n\nJarvis calls Thu Kha Su San only ဆရာ. A guest in the secretary chat is not the boss, and must never be called ဆရာ.\n\nReplies to guests are one or two short sentences of everyday Burmese, not a customer-service essay. On a first reply, Jarvis introduces itself as the AI secretary speaking because ဆရာ is offline or busy. See [[Secretary Front Desk]] and [[Myanmar Sleep Window]].\n",
      "path": "notes/Biography/Jarvis Addresses the Boss.md"
    },
    {
      "id": 1,
      "label": "Myanmar Sleep Window",
      "group": "Biography",
      "excerpt": "# Myanmar Sleep Window\n\nThu Kha Su San's sleeping hours are 11:00 PM to 7:00 AM, Myanmar time. Friends who write during that window are told he is likely asleep and that an important note can wait until morning.\n\nThe secretary still answers ordinary guest messages at night. It does not refuse a conversation only because the clock is inside the sleep window. See [[Jarvis Addresses the Boss]].\n",
      "path": "notes/Biography/Myanmar Sleep Window.md"
    },
    {
      "id": 2,
      "label": "Public Hosts",
      "group": "Biography",
      "excerpt": "# Public Hosts\n\nTwo hostnames are the public doors. The voice desk is https://jarvis.thukha.online, described in [[Jarvis HUD Address]]. Mickey's firmware update address is https://phoelone.thukha.online/xiaozhi/ota/, described in [[Phoelone OTA Address]].\n\nNeither host is opened by publishing VPS ports 80 or 443. Jarvis reaches the internet through a Cloudflare tunnel to 127.0.0.1:8000. See [[Cloudflare Tunnel]].\n",
      "path": "notes/Biography/Public Hosts.md"
    },
    {
      "id": 3,
      "label": "Thu Kha Su San",
      "group": "Biography",
      "excerpt": "# Thu Kha Su San\n\nThu Kha Su San is the owner of these notes and the person Jarvis calls ဆရာ. He builds two desks of work: the Mickey robot in the phoelone repository, and the Jarvis secretary that runs on his VPS.\n\nHis public TikTok is [[TikTok Profile]]. The robot facts live under Mickey and Hardware. The voice desk lives under Jarvis. The two meet in [[Two Products One Desk]].\n",
      "path": "notes/Biography/Thu Kha Su San.md"
    },
    {
      "id": 4,
      "label": "TikTok Profile",
      "group": "Biography",
      "excerpt": "# TikTok Profile\n\nThe public account is @thukhatech, display name ThuKha Industries. The profile header shows 482 following, 10.2K followers, and 79.3K likes. Individual video captions were not readable from the public page.\n\nThe bio line is: Telegram @thukhasusan78, phone 09784679389, Movie Channel @thukhamovies. That phone is the contact printed on the profile, not a note about how to take payment. See [[Thu Kha Su San]] and [[VIP Channel Subscription]].\n",
      "path": "notes/Biography/TikTok Profile.md"
    },
    {
      "id": 5,
      "label": "Two Products One Desk",
      "group": "Biography",
      "excerpt": "# Two Products One Desk\n\nMickey is the ESP32-S3 desktop robot. Jarvis is the secretary and voice HUD on the VPS. They are separate codebases: phoelone is the firmware, jarvis-backup is the Python desk.\n\nMickey's companion server is not written inside the firmware repo. It lives on a remote VPS. Jarvis is that style of always-on desk for ဆရာ, not the robot's motor loop. See [[Mickey the Desktop Robot]] and [[Voice HUD]].\n",
      "path": "notes/Biography/Two Products One Desk.md"
    },
    {
      "id": 6,
      "label": "Companion on a VPS",
      "group": "Business",
      "excerpt": "# Companion on a VPS\n\nMickey's companion is hosted on a remote VPS, described in the firmware notes as a DigitalOcean machine reached by SSH. The firmware repo does not contain the FastAPI app.\n\nThe companion may keep the socket open while the robot is idle so a dashboard can send MCP without a wake word. Deep sleep still closes that socket. Idle personality does not depend on it. See [[Idle Life Without the Cloud]] and [[Custom Backend Not XiaoZhi Cloud]].\n",
      "path": "notes/Business/Companion on a VPS.md"
    },
    {
      "id": 7,
      "label": "Custom Backend Not XiaoZhi Cloud",
      "group": "Business",
      "excerpt": "# Custom Backend Not XiaoZhi Cloud\n\nMickey does not use the official XiaoZhi cloud. The goal is a custom local backend. The ESP32 client stays as close to upstream as possible, and only the server address and Wi-Fi credentials change.\n\nThe backend is a remote FastAPI service. Its JSON and MCP shapes are a frozen wire contract. Python for that server is not added inside the firmware repository. See [[Companion on a VPS]] and [[WebSocket and Opus]].\n",
      "path": "notes/Business/Custom Backend Not XiaoZhi Cloud.md"
    },
    {
      "id": 8,
      "label": "Idle Life Without the Cloud",
      "group": "Business",
      "excerpt": "# Idle Life Without the Cloud\n\nMickey's idle life has to work with the WebSocket closed. Face, sway, pickup, and fall reactions are local. The companion socket is for the dashboard, not the body's heartbeat.\n\nThe same rule governs vision: a missing or stuck camera coprocessor leaves Mickey a voice robot. Cloud captions are optional and slow. See [[Idle Director]] and [[Vision Coprocessor Decision]].\n",
      "path": "notes/Business/Idle Life Without the Cloud.md"
    },
    {
      "id": 9,
      "label": "Jarvis HUD Address",
      "group": "Business",
      "excerpt": "# Jarvis HUD Address\n\nThe voice HUD is served at https://jarvis.thukha.online. The path is Chrome or Edge, then Cloudflare Access, then a Cloudflare tunnel, then Jarvis bound to 127.0.0.1:8000.\n\n`/ws/voice` is left unauthenticated in the app on purpose. The lock is Access, an origin allowlist of that hostname, and the localhost bind. See [[Cloudflare Tunnel]] and [[Voice HUD]].\n",
      "path": "notes/Business/Jarvis HUD Address.md"
    },
    {
      "id": 10,
      "label": "Mickey the Desktop Robot",
      "group": "Business",
      "excerpt": "# Mickey the Desktop Robot\n\nMickey is an ESP32-S3 desktop AI robot in the style of an EMO desk companion. It uses the open-source XiaoZhi client for voice, networking, display, and device control. It must not copy LivingAI firmware, assets, branding, cloud APIs, or industrial design.\n\nThe board profile is `mickey`: no camera on the main chip, no hands, four servos. Personality is face, body, and voice. See [[Mickey Board Identity]] and [[No Hands Four Servos]].\n",
      "path": "notes/Business/Mickey the Desktop Robot.md"
    },
    {
      "id": 11,
      "label": "Phoelone OTA Address",
      "group": "Business",
      "excerpt": "# Phoelone OTA Address\n\nProduction OTA is on. `CONFIG_OTA_URL` is https://phoelone.thukha.online/xiaozhi/ota/. On boot the device POSTs, then stores `websocket.url` and the token in NVS.\n\nThe board type in that POST is `mickey`, not `otto-robot`. A lab server that advertises firmware `0.0.0` makes the upgrade step skip. See [[OTA and Firmware Version]] and [[Public Hosts]].\n",
      "path": "notes/Business/Phoelone OTA Address.md"
    },
    {
      "id": 12,
      "label": "VIP Channel Subscription",
      "group": "Business",
      "excerpt": "# VIP Channel Subscription\n\nThe Jarvis catalog lists a Telegram VIP channel subscription at 35,000 MMK. The public [[TikTok Profile]] bio also names a movie channel, @thukhamovies.\n\nThis note is only the product name and the catalog price. It does not store payment steps, invite-link generation, or a media library.\n",
      "path": "notes/Business/VIP Channel Subscription.md"
    },
    {
      "id": 13,
      "label": "Voice Roadmap",
      "group": "Business",
      "excerpt": "# Voice Roadmap\n\nThe Jarvis spec lists a later voice desk, in this order of impact: barge-in on a live audio stream, a spoken wake word, a boot briefing, a tool-activity rail, an audio-reactive reactor, server-side speech recognition, an optional camera greeting, and a home-screen app.\n\nAlways-listening on the open internet is intentionally out. Access and the origin allowlist stay. See [[Barge In Roadmap]] and [[Wake Word Roadmap]].\n",
      "path": "notes/Business/Voice Roadmap.md"
    },
    {
      "id": 14,
      "label": "Fitness Windows Should Be Naia Millisecond",
      "group": "captures",
      "excerpt": "# Fitness Windows Should Be Naia Millisecond\n\n2026-09-26\n\nthe fitness windows should be naia millisecond\n\n[[Thu Kha Su San]]\n",
      "path": "notes/captures/Fitness Windows Should Be Naia Millisecond.md"
    },
    {
      "id": 15,
      "label": "My Favourite Meal Is Shan Noodle",
      "group": "captures",
      "excerpt": "# My Favourite Meal Is Shan Noodle\n\n2026-09-26\n\nmy favourite meal is Shan Noodle.\n\n[[Thu Kha Su San]]\n",
      "path": "notes/captures/My Favourite Meal Is Shan Noodle.md"
    },
    {
      "id": 16,
      "label": "Display Pins",
      "group": "Hardware",
      "excerpt": "# Display Pins\n\nDisplay SPI: clock GPIO 9, data GPIO 10, reset GPIO 11, data/command GPIO 46. Backlight is GPIO 3. Chip-select is tied to ground, not a GPIO. GPIO 12 stays the LCD chip-select net only and is not a servo.\n\nDo not move the backlight to free a camera clock. The vision plan keeps this panel on the main chip. See [[Display and Emotions]] and [[Vision Coprocessor Decision]].\n",
      "path": "notes/Hardware/Display Pins.md"
    },
    {
      "id": 17,
      "label": "Do Not Remap Pins",
      "group": "Hardware",
      "excerpt": "# Do Not Remap Pins\n\nGOAL.md and the August production plan both say the assembled wiring already matches `config.h`. New modules are added on free pins, or on the coprocessor, not by moving audio, display, or servo pins.\n\nConfirm GPIO 1, 2, 8, and 13 on the bench before soldering a vision board. Do not invent replacements. A build that compiles is not a hardware test. See [[Frozen GPIO Map]] and [[Build Command]].\n",
      "path": "notes/Hardware/Do Not Remap Pins.md"
    },
    {
      "id": 18,
      "label": "Microphone and Speaker Pins",
      "group": "Hardware",
      "excerpt": "# Microphone and Speaker Pins\n\nMicrophone I2S: WS GPIO 4, SCK GPIO 5, data GPIO 6. Speaker amplifier I2S: data GPIO 7, bit clock GPIO 15, word select GPIO 16.\n\nGPIO 15 and 16 are not free for a camera. Simplex audio means capture and playback do not overlap on this board. See [[Audio Path]] and [[Frozen GPIO Map]].\n",
      "path": "notes/Hardware/Microphone and Speaker Pins.md"
    },
    {
      "id": 19,
      "label": "Sensor Pins",
      "group": "Hardware",
      "excerpt": "# Sensor Pins\n\nMPU6050: SDA GPIO 41, SCL GPIO 42, interrupt GPIO 40. TTP223 touch: GPIO 47. The sensor task samples both. IMU and touch MCP report `wired: true`. A light sensor is not started and still reports `wired: false`.\n\nPickup and fall use the motion sensor and cancel idle fidget. See [[Idle Director]] and [[Low Battery Inhibit]].\n",
      "path": "notes/Hardware/Sensor Pins.md"
    },
    {
      "id": 20,
      "label": "Servo Pins",
      "group": "Hardware",
      "excerpt": "# Servo Pins\n\nFour servos, frozen: right leg GPIO 39, right foot GPIO 38, left leg GPIO 17, left foot GPIO 18. Hands are not wired.\n\nServos need their own power rail and a shared ground. They must not be powered from an ESP32-S3 pin or a weak 3.3 volt rail. First flash is done with servo power disconnected. See [[No Hands Four Servos]] and [[Frozen GPIO Map]].\n",
      "path": "notes/Hardware/Servo Pins.md"
    },
    {
      "id": 21,
      "label": "Vision Interconnect Pins",
      "group": "Hardware",
      "excerpt": "# Vision Interconnect Pins\n\nThe proposed link to a camera coprocessor uses four free GPIOs, and only after a bench check: GPIO 8 as interrupt input, GPIO 1 as UART receive, GPIO 2 as UART transmit, GPIO 13 as reset output. UART is 921600 8N1.\n\nGPIO 45 and 48 stay unused. Photos are not on this link unless a capture was requested. A heartbeat timeout pulses reset. After three failed recovers, vision is marked unwired and the main robot keeps talking. See [[Vision Coprocessor Decision]] and [[Do Not Remap Pins]].\n",
      "path": "notes/Hardware/Vision Interconnect Pins.md"
    },
    {
      "id": 22,
      "label": "Barge In Roadmap",
      "group": "Jarvis",
      "excerpt": "# Barge In Roadmap\n\nBarge-in is the first unbuilt item on the voice roadmap. The idea is a Gemini Live style bidirectional audio stream so ဆရာ can interrupt a sentence, instead of the current chain of browser speech, then the model, then edge-tts clips.\n\nUntil that exists, the HUD finishes the spoken reply. The chunked pipeline stays. See [[Voice Roadmap]] and [[Burmese Voice Engine]].\n",
      "path": "notes/Jarvis/Barge In Roadmap.md"
    },
    {
      "id": 23,
      "label": "Burmese Voice Engine",
      "group": "Jarvis",
      "excerpt": "# Burmese Voice Engine\n\nSpoken replies use edge-tts, voice `my-MM-ThihaNeural`. Audio is MP3, decoded in the browser. This replaced a Gemini preview TTS voice named Enceladus after invalid-key errors, empty audio, and text-instead-of-speech responses.\n\n`VOICE_MODEL` and `VOICE_NAME` in config are leftovers from that Gemini attempt and are unused. Edge TTS needs no extra API key. The VPS must reach `speech.platform.bing.com`. See [[Voice HUD]] and [[Voice Tool Loop]].\n",
      "path": "notes/Jarvis/Burmese Voice Engine.md"
    },
    {
      "id": 24,
      "label": "Cloudflare Tunnel",
      "group": "Jarvis",
      "excerpt": "# Cloudflare Tunnel\n\nJarvis binds `HOST=127.0.0.1` and `PORT=8000`. `VOICE_ALLOWED_ORIGINS` is https://jarvis.thukha.online. A cloudflared tunnel named jarvis-web is the only ingress. DNS for that name is proxied. Ports 80 and 443 on the VPS stay closed.\n\nCloudflare Access covers the whole hostname, including `/ws/voice`. The policy allows the owner's email only. The WebSocket sends the Access cookie because it is same-origin. Health is `GET /health` on localhost. See [[Jarvis HUD Address]] and [[Watchdog Soft Restart]].\n",
      "path": "notes/Jarvis/Cloudflare Tunnel.md"
    },
    {
      "id": 25,
      "label": "Desk Roles",
      "group": "Jarvis",
      "excerpt": "# Desk Roles\n\nRoles named in the runbook are ceo, sysadmin, researcher, deep_researcher, business_manager, and secretary. Voice on the HUD runs as ceo with delegation tools removed. Telegram can still use the researcher path.\n\nTools declare an owner role. Execution checks that role, not only the declaration. Custom skills default to ceo. Web search is visible to the voice ceo so a news question does not have to leave the speaker. See [[Voice Cannot Delegate]] and [[Secretary Front Desk]].\n",
      "path": "notes/Jarvis/Desk Roles.md"
    },
    {
      "id": 26,
      "label": "HUD Widgets",
      "group": "Jarvis",
      "excerpt": "# HUD Widgets\n\n`show_hologram` opens a draggable popup. Live widgets are map, weather, schedule, tasks, system vitals, report, and image. Weather comes from wttr.in. System vitals come from psutil. Schedule lists APScheduler jobs. Tasks list ongoing items from memory.\n\nThe popup close button is 28 pixels so a finger can hit it. Dragging the header ignores taps on that button. Say \"show my schedules\", \"what tasks are ongoing\", or \"system vitals\" and the card appears while Jarvis speaks. See [[Voice Tool Loop]] and [[Scheduler]].\n",
      "path": "notes/Jarvis/HUD Widgets.md"
    },
    {
      "id": 27,
      "label": "Memory and Tasks",
      "group": "Jarvis",
      "excerpt": "# Memory and Tasks\n\nChat memory, the business ledger, and the movie repository use SQLite in WAL mode with a busy timeout, through `core/db.py`. Voice history on one socket is the last 12 turns and is separate from the long-term store.\n\nOngoing tasks are what the HUD task widget reads. A tool that messages ဆရာ on Telegram must also save that text into chat history after a successful send. See [[HUD Widgets]] and [[Desk Roles]].\n",
      "path": "notes/Jarvis/Memory and Tasks.md"
    },
    {
      "id": 28,
      "label": "Mobile HUD Reconnect",
      "group": "Jarvis",
      "excerpt": "# Mobile HUD Reconnect\n\nA slow phone turn was often a half-open socket, not a slow model. The server ping is every 20 seconds. If the page hears nothing for 45 seconds, it closes and reconnects. Backoff starts at 500 milliseconds and stops at 3 seconds.\n\nText spoken while the socket is down is held and sent when it opens. Returning to the tab after 30 seconds of silence treats the socket as dead. A screen wake lock is requested on boot. The status line shows CONNECTING, SECURE, or RECONNECTING. See [[Voice HUD]] and [[Burmese Voice Engine]].\n",
      "path": "notes/Jarvis/Mobile HUD Reconnect.md"
    },
    {
      "id": 29,
      "label": "Scheduler",
      "group": "Jarvis",
      "excerpt": "# Scheduler\n\nScheduled jobs live in APScheduler. The HUD schedule widget shows each job id, a short prompt preview, and the next run. A boot briefing was proposed and not required yet: time, weather, and jobs due today, pushed when the HUD connects.\n\nThe scheduler does not replace a spoken answer. Voice still speaks the one-line summary. See [[HUD Widgets]] and [[Voice Roadmap]].\n",
      "path": "notes/Jarvis/Scheduler.md"
    },
    {
      "id": 30,
      "label": "Secretary Front Desk",
      "group": "Jarvis",
      "excerpt": "# Secretary Front Desk\n\nThe secretary is the Telegram front desk for guests. It speaks short Burmese, introduces itself as Jarvis on a first reply, and does not run technical tools or payment checks itself. Complex work is delegated to a backend role.\n\nA missed-call marker gets an apology and an offer to leave a message. Guests can type /clear, /new, or /restart to wipe that chat. Privacy rule: do not share personal details of ဆရာ. See [[Jarvis Addresses the Boss]] and [[Myanmar Sleep Window]].\n",
      "path": "notes/Jarvis/Secretary Front Desk.md"
    },
    {
      "id": 31,
      "label": "Voice Cannot Delegate",
      "group": "Jarvis",
      "excerpt": "# Voice Cannot Delegate\n\nVoice mode strips `delegate_task`, `report_to_sir`, and `publish_event` from the tool list. The CEO prompt used to force news through a researcher, which made answers arrive on Telegram instead of the speaker. The strip is deterministic, so the model cannot delegate on the HUD.\n\nThe Telegram path leaves voice mode off and can still delegate. Each HUD socket keeps the last 12 turns and passes them as chat history. See [[Voice Tool Loop]] and [[Desk Roles]].\n",
      "path": "notes/Jarvis/Voice Cannot Delegate.md"
    },
    {
      "id": 32,
      "label": "Voice HUD",
      "group": "Jarvis",
      "excerpt": "# Voice HUD\n\nThe HUD is the page in `interfaces/web/`. The owner taps to initialize, grants the microphone, and speaks. Burmese speech-to-text in Chrome or Edge uses the browser Web Speech API (`my-MM`). Safari and Firefox only see a notice that those browsers are not supported.\n\nA text box under the transcript sends the same payload as speech, so a phone with a broken speech service can still type. See [[Burmese Voice Engine]] and [[Jarvis HUD Address]].\n",
      "path": "notes/Jarvis/Voice HUD.md"
    },
    {
      "id": 33,
      "label": "Voice Tool Loop",
      "group": "Jarvis",
      "excerpt": "# Voice Tool Loop\n\nThe voice brain runs a tool feedback loop of at most three rounds. Function calls go back to the model, results come back as function responses, and the spoken answer keeps streaming. Each tool result is truncated to 4000 characters.\n\nA news question should call search directly and answer in short spoken Burmese. It must not hand the turn to Telegram. Simple turns were aimed at about 2 to 4 seconds. A search turn was aimed at about 5 to 7 seconds. See [[Voice Cannot Delegate]] and [[HUD Widgets]].\n",
      "path": "notes/Jarvis/Voice Tool Loop.md"
    },
    {
      "id": 34,
      "label": "Wake Word Roadmap",
      "group": "Jarvis",
      "excerpt": "# Wake Word Roadmap\n\nA Jarvis wake word on the HUD is proposed, not built. Candidates named in the spec are Picovoice Porcupine or openWakeWord on the server, so a dedicated screen would not need a tap. Browsers would still fall back to a tap.\n\nThis is separate from Mickey's on-device ESP-SR wake word. See [[Wake Word and Boot Button]] and [[Voice Roadmap]].\n",
      "path": "notes/Jarvis/Wake Word Roadmap.md"
    },
    {
      "id": 35,
      "label": "Watchdog Soft Restart",
      "group": "Jarvis",
      "excerpt": "# Watchdog Soft Restart\n\n`watchdog.py` checks that the process is listening, then soft-restarts if it is not. It must not run git fetch, git reset, or git clean. A hard reset on 13 August 2026 wiped uncommitted work.\n\nIf the soft restart fails, the watchdog sends a fatal Telegram note and waits. The git tree is left alone. See [[Cloudflare Tunnel]] and [[Memory and Tasks]].\n",
      "path": "notes/Jarvis/Watchdog Soft Restart.md"
    },
    {
      "id": 36,
      "label": "Audio Path",
      "group": "Mickey",
      "excerpt": "# Audio Path\n\nAudio is simplex I2S. The microphone and the speaker do not run full duplex on this board. While Mickey is speaking, microphone streaming stops unless a realtime echo-cancellation mode is on, and this SKU sets `audio_use_simplex` true.\n\nCamera-board I2C on GPIO 15 and 16 is the speaker clock on Mickey. Those pins stay audio. See [[Microphone and Speaker Pins]] and [[Wake Word and Boot Button]].\n",
      "path": "notes/Mickey/Audio Path.md"
    },
    {
      "id": 37,
      "label": "Build Command",
      "group": "Mickey",
      "excerpt": "# Build Command\n\nThe canonical build is `python scripts/build.py mickey --name mickey --language en-US` from the phoelone repo, after ESP-IDF v6.0.2 is installed and the target is esp32s3.\n\nThe first build downloads the component manager tree and can take more than 30 minutes on Windows. Linux is the faster path. Disconnect servo power for the first flash. Serial monitor is 115200 baud. See [[Mickey Board Identity]] and [[Do Not Remap Pins]].\n",
      "path": "notes/Mickey/Build Command.md"
    },
    {
      "id": 38,
      "label": "Device MCP Tools",
      "group": "Mickey",
      "excerpt": "# Device MCP Tools\n\nDevice tools already on the board include `self.get_device_status`, volume, brightness, theme, `self.otto.*`, `self.battery.get_level`, and `self.otto.get_ip`. Motion tools to keep are action, stop, status, trim, and servo sequences.\n\n`self.otto.stop` is cooperative. It does not delete the task. The oscillator re-attaches, holds PWM, and `Home()` returns to 90 degrees. A later Mickey namespace would be `self.mickey.*`. User firmware upgrade is `self.upgrade_firmware`. See [[No Hands Four Servos]].\n",
      "path": "notes/Mickey/Device MCP Tools.md"
    },
    {
      "id": 39,
      "label": "Display and Emotions",
      "group": "Mickey",
      "excerpt": "# Display and Emotions\n\nThe panel is an ST7789-style SPI display. Expressions are GIFs through `Display::SetEmotion`. After UI setup the idle face is `staticstate`.\n\nBacklight is GPIO 3. Chip-select is strapped to ground, so GPIO 12 is not a hand pin. Vision reactions change the face first, then a small body move. See [[Display Pins]] and [[Idle Director]].\n",
      "path": "notes/Mickey/Display and Emotions.md"
    },
    {
      "id": 40,
      "label": "Face ID Five Slots",
      "group": "Mickey",
      "excerpt": "# Face ID Five Slots\n\nFace ID of five people stays on the coprocessor. Embeddings live in that chip's NVS. The main board stores only slot-to-name in its own NVS. Recognition does not need the cloud.\n\nEnroll sends a slot number. The coprocessor captures several frames, builds an embedding, and acknowledges. Forget clears a slot. Unknown faces get the generic enter reaction. Photos upload only when someone asks. The camera is not streamed. See [[Vision Reflex Table]] and [[Vision Coprocessor Decision]].\n",
      "path": "notes/Mickey/Face ID Five Slots.md"
    },
    {
      "id": 41,
      "label": "Frozen GPIO Map",
      "group": "Mickey",
      "excerpt": "# Frozen GPIO Map\n\nThe assembled Mickey is wired to the Otto no-camera map in `main/boards/mickey/config.h`. Those pins are frozen. Do not edit them to match a camera-Otto sketch.\n\nAn older camera-Otto list (legs on 43 and 5, feet on 44 and 6, hands on 4 and 7) is a different starting profile. Flashing that list onto this robot hits the microphone and speaker. The live map is in [[Servo Pins]], [[Microphone and Speaker Pins]], [[Display Pins]], and [[Sensor Pins]]. See [[Do Not Remap Pins]].\n",
      "path": "notes/Mickey/Frozen GPIO Map.md"
    },
    {
      "id": 42,
      "label": "Idle Director",
      "group": "Mickey",
      "excerpt": "# Idle Director\n\nThe idle director is done, in `mickey_behavior.cc`. Face GIFs run in the idle state. Body motion waits until 60 seconds of inactivity, then a slow swing and an occasional reduced walk.\n\nWake word, pet, pickup, fall, a dashboard `self.otto.*` call, and deep sleep all cancel the fidget. Companion keepalive can hold `/xiaozhi/v1/` open during idle so the dashboard can dance the robot without a wake word. The fidget itself still runs if that socket is closed. See [[Idle Life Without the Cloud]] and [[Low Battery Inhibit]].\n",
      "path": "notes/Mickey/Idle Director.md"
    },
    {
      "id": 43,
      "label": "Low Battery Inhibit",
      "group": "Mickey",
      "excerpt": "# Low Battery Inhibit\n\nLow-battery motion inhibit is done. Below the NVS value `mickey/low_bat_pct`, default 15 percent, and while not charging, Mickey rejects walk, jump, dance, and fidget. Home and stop still work.\n\nThe backlight dims, the face goes sleepy, and a low-battery sound plays on entry and again every 10 minutes. See [[Idle Director]] and [[Sensor Pins]].\n",
      "path": "notes/Mickey/Low Battery Inhibit.md"
    },
    {
      "id": 44,
      "label": "Mickey Board Identity",
      "group": "Mickey",
      "excerpt": "# Mickey Board Identity\n\nThe firmware profile is the `mickey` board under `main/boards/mickey`, chip ESP32-S3, module N16R8: 16 MB flash and 8 MB octal PSRAM. Preferred SDK is ESP-IDF v6.0.2. PlatformIO is not used.\n\nOTA identity is `mickey`. Do not ship this robot as `otto-robot`. SoftAP and hostname prefix are Mickey. Firmware language for this profile is English (`en-US`); Myanmar is not one of the built-in interface locales. See [[Build Command]] and [[Phoelone OTA Address]].\n",
      "path": "notes/Mickey/Mickey Board Identity.md"
    },
    {
      "id": 45,
      "label": "No Hands Four Servos",
      "group": "Mickey",
      "excerpt": "# No Hands Four Servos\n\nThis SKU has no hand servos. GPIO 12 is LCD chip-select only. Hand actions such as hands up, hand wave, greeting, and arm swing are removed and must not be called from vision or MCP.\n\nMotion is four servos: right leg, right foot, left leg, left foot. Face GIFs and voice carry the personality. See [[Servo Pins]] and [[Vision Reflex Table]].\n",
      "path": "notes/Mickey/No Hands Four Servos.md"
    },
    {
      "id": 46,
      "label": "OTA and Firmware Version",
      "group": "Mickey",
      "excerpt": "# OTA and Firmware Version\n\nFull download, flash, and reboot machinery exists. The lab server has advertised a dummy version `0.0.0`, so upgrade is skipped until a real version is published. Stock activation UI (a spoken digit code) runs only if the OTA JSON includes `activation`. The lab VPS omits it.\n\nBoard `config.json` type and name are `mickey`. Kconfig symbol is `BOARD_TYPE_MICKEY`. See [[Phoelone OTA Address]] and [[Mickey Board Identity]].\n",
      "path": "notes/Mickey/OTA and Firmware Version.md"
    },
    {
      "id": 47,
      "label": "Vision Coprocessor Decision",
      "group": "Mickey",
      "excerpt": "# Vision Coprocessor Decision\n\nThe main ESP32-S3 has no pins and no spare CPU for a camera. Mic, speaker, display, four servos, the motion sensor, and the touch key already occupy it. A DVP camera needs about eleven pins, and the frozen map forbids a remap.\n\nThe decision is a second chip that owns the camera and on-device models. The main board only receives tiny events and, when asked, one JPEG. Default module is another ESP32-S3 with a camera, such as XIAO ESP32-S3 Sense, with Wi-Fi off. Himax WiseEye2 is the low-power alternative and is weaker at five-person face ID. ESP32-P4 is a later upgrade, not required for reflexes. See [[Vision Interconnect Pins]] and [[Face ID Five Slots]].\n",
      "path": "notes/Mickey/Vision Coprocessor Decision.md"
    },
    {
      "id": 48,
      "label": "Vision Reflex Table",
      "group": "Mickey",
      "excerpt": "# Vision Reflex Table\n\nReflexes are local and have no hands. An unknown person entering gets a curious or surprised face and a small sway. A known face, slots 0 to 4, gets that person's emotion clip. Gaze holds eye contact and pauses idle fidget. A wave from the user gets a happy face and a swing or a reduced walk, never a hand wave. Leave returns to idle with a soft sad or static face. A phone in view gets an ignored or embarrassed look.\n\nThe perceived budget is under 200 milliseconds with the WebSocket closed. Scene classes are a closed set: none, present, approach, leave, gaze, wave, phone, unknown. Open-ended questions go to the companion vision model only when asked. See [[No Hands Four",
      "path": "notes/Mickey/Vision Reflex Table.md"
    },
    {
      "id": 49,
      "label": "Wake Word and Boot Button",
      "group": "Mickey",
      "excerpt": "# Wake Word and Boot Button\n\nOn the device today, wake word is ESP-SR AFE with the `wn9_nihaoxiaozhi_tts` model. GPIO 0 toggles chat. A click during `kDeviceStateStarting` enters Wi-Fi config. There is no idle long-press to re-pair.\n\nA later spec phase mentions MultiNet phrases for Mickey, Hey Mickey, and Hi Mickey, including Burmese-accented syllables. That phrase set is not what the August production note says is already running. See [[Wi-Fi First Boot]] and [[Audio Path]].\n",
      "path": "notes/Mickey/Wake Word and Boot Button.md"
    },
    {
      "id": 50,
      "label": "WebSocket and Opus",
      "group": "Mickey",
      "excerpt": "# WebSocket and Opus\n\nThe live link is WebSocket hello with raw Opus version 1: listen, start, detect, abort. MCP rides inside JSON as `type: mcp`. MQTT plus UDP exists upstream and is not the Mickey lab path described in the production plan.\n\nA JSON `ping` gets a `pong`. There should be no \"unknown message type: ping\". A LAN debug MCP server listens on port 8080 at `/ws`. That port is not the cloud protocol. See [[Custom Backend Not XiaoZhi Cloud]] and [[Device MCP Tools]].\n",
      "path": "notes/Mickey/WebSocket and Opus.md"
    },
    {
      "id": 51,
      "label": "Wi-Fi First Boot",
      "group": "Mickey",
      "excerpt": "# Wi-Fi First Boot\n\nProvisioning is a soft access point by default. BluFi exists in Kconfig and is not enabled on mickey. The access-point name starts with Mickey.\n\nA wrong Wi-Fi password needs a 60 second timeout or an NVS erase. `SystemReset` is unused, same as stock Otto. Boot still reconnects and closes the audio channel if Wi-Fi drops. See [[Wake Word and Boot Button]].\n",
      "path": "notes/Mickey/Wi-Fi First Boot.md"
    }
  ],
  "links": [
    {
      "source": 0,
      "target": 30
    },
    {
      "source": 0,
      "target": 1
    },
    {
      "source": 0,
      "target": 3
    },
    {
      "source": 1,
      "target": 3
    },
    {
      "source": 2,
      "target": 9
    },
    {
      "source": 2,
      "target": 11
    },
    {
      "source": 2,
      "target": 24
    },
    {
      "source": 3,
      "target": 4
    },
    {
      "source": 3,
      "target": 5
    },
    {
      "source": 4,
      "target": 12
    },
    {
      "source": 5,
      "target": 10
    },
    {
      "source": 5,
      "target": 32
    },
    {
      "source": 6,
      "target": 8
    },
    {
      "source": 6,
      "target": 7
    },
    {
      "source": 7,
      "target": 50
    },
    {
      "source": 8,
      "target": 42
    },
    {
      "source": 8,
      "target": 47
    },
    {
      "source": 9,
      "target": 24
    },
    {
      "source": 9,
      "target": 32
    },
    {
      "source": 10,
      "target": 44
    },
    {
      "source": 10,
      "target": 45
    },
    {
      "source": 11,
      "target": 46
    },
    {
      "source": 13,
      "target": 22
    },
    {
      "source": 13,
      "target": 34
    },
    {
      "source": 3,
      "target": 14
    },
    {
      "source": 3,
      "target": 15
    },
    {
      "source": 16,
      "target": 39
    },
    {
      "source": 16,
      "target": 47
    },
    {
      "source": 17,
      "target": 41
    },
    {
      "source": 17,
      "target": 37
    },
    {
      "source": 18,
      "target": 36
    },
    {
      "source": 18,
      "target": 41
    },
    {
      "source": 19,
      "target": 42
    },
    {
      "source": 19,
      "target": 43
    },
    {
      "source": 20,
      "target": 45
    },
    {
      "source": 20,
      "target": 41
    },
    {
      "source": 21,
      "target": 47
    },
    {
      "source": 17,
      "target": 21
    },
    {
      "source": 22,
      "target": 23
    },
    {
      "source": 23,
      "target": 32
    },
    {
      "source": 23,
      "target": 33
    },
    {
      "source": 24,
      "target": 35
    },
    {
      "source": 25,
      "target": 31
    },
    {
      "source": 25,
      "target": 30
    },
    {
      "source": 26,
      "target": 33
    },
    {
      "source": 26,
      "target": 29
    },
    {
      "source": 26,
      "target": 27
    },
    {
      "source": 25,
      "target": 27
    },
    {
      "source": 28,
      "target": 32
    },
    {
      "source": 23,
      "target": 28
    },
    {
      "source": 13,
      "target": 29
    },
    {
      "source": 1,
      "target": 30
    },
    {
      "source": 31,
      "target": 33
    },
    {
      "source": 34,
      "target": 49
    },
    {
      "source": 27,
      "target": 35
    },
    {
      "source": 36,
      "target": 49
    },
    {
      "source": 37,
      "target": 44
    },
    {
      "source": 38,
      "target": 45
    },
    {
      "source": 39,
      "target": 42
    },
    {
      "source": 40,
      "target": 48
    },
    {
      "source": 40,
      "target": 47
    },
    {
      "source": 16,
      "target": 41
    },
    {
      "source": 19,
      "target": 41
    },
    {
      "source": 42,
      "target": 43
    },
    {
      "source": 11,
      "target": 44
    },
    {
      "source": 45,
      "target": 48
    },
    {
      "source": 44,
      "target": 46
    },
    {
      "source": 49,
      "target": 51
    },
    {
      "source": 38,
      "target": 50
    }
  ]
};
