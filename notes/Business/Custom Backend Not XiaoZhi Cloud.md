# Custom Backend Not XiaoZhi Cloud

Mickey does not use the official XiaoZhi cloud. The goal is a custom local backend. The ESP32 client stays as close to upstream as possible, and only the server address and Wi-Fi credentials change.

The backend is a remote FastAPI service. Its JSON and MCP shapes are a frozen wire contract. Python for that server is not added inside the firmware repository. See [[Companion on a VPS]] and [[WebSocket and Opus]].
