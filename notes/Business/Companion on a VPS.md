# Companion on a VPS

Mickey's companion is hosted on a remote VPS, described in the firmware notes as a DigitalOcean machine reached by SSH. The firmware repo does not contain the FastAPI app.

The companion may keep the socket open while the robot is idle so a dashboard can send MCP without a wake word. Deep sleep still closes that socket. Idle personality does not depend on it. See [[Idle Life Without the Cloud]] and [[Custom Backend Not XiaoZhi Cloud]].
