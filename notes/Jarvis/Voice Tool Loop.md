# Voice Tool Loop

The voice brain runs a tool feedback loop of at most three rounds. Function calls go back to the model, results come back as function responses, and the spoken answer keeps streaming. Each tool result is truncated to 4000 characters.

A news question should call search directly and answer in short spoken Burmese. It must not hand the turn to Telegram. Simple turns were aimed at about 2 to 4 seconds. A search turn was aimed at about 5 to 7 seconds. See [[Voice Cannot Delegate]] and [[HUD Widgets]].
