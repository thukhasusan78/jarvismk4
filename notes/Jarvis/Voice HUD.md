# Voice HUD

The HUD is the page in `interfaces/web/`. The owner taps to initialize, grants the microphone, and speaks. Burmese speech-to-text in Chrome or Edge uses the browser Web Speech API (`my-MM`). Safari and Firefox only see a notice that those browsers are not supported.

A text box under the transcript sends the same payload as speech, so a phone with a broken speech service can still type. See [[Burmese Voice Engine]] and [[Jarvis HUD Address]].
