# Memory and Tasks

Chat memory, the business ledger, and the movie repository use SQLite in WAL mode with a busy timeout, through `core/db.py`. Voice history on one socket is the last 12 turns and is separate from the long-term store.

Ongoing tasks are what the HUD task widget reads. A tool that messages ဆရာ on Telegram must also save that text into chat history after a successful send. See [[HUD Widgets]] and [[Desk Roles]].
