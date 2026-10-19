# WebSocket and Opus

The live link is WebSocket hello with raw Opus version 1: listen, start, detect, abort. MCP rides inside JSON as `type: mcp`. MQTT plus UDP exists upstream and is not the Mickey lab path described in the production plan.

A JSON `ping` gets a `pong`. There should be no "unknown message type: ping". A LAN debug MCP server listens on port 8080 at `/ws`. That port is not the cloud protocol. See [[Custom Backend Not XiaoZhi Cloud]] and [[Device MCP Tools]].
