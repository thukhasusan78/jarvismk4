# Voice Cannot Delegate

Voice mode strips `delegate_task`, `report_to_sir`, and `publish_event` from the tool list. The CEO prompt used to force news through a researcher, which made answers arrive on Telegram instead of the speaker. The strip is deterministic, so the model cannot delegate on the HUD.

The Telegram path leaves voice mode off and can still delegate. Each HUD socket keeps the last 12 turns and passes them as chat history. See [[Voice Tool Loop]] and [[Desk Roles]].
