# Device MCP Tools

Device tools already on the board include `self.get_device_status`, volume, brightness, theme, `self.otto.*`, `self.battery.get_level`, and `self.otto.get_ip`. Motion tools to keep are action, stop, status, trim, and servo sequences.

`self.otto.stop` is cooperative. It does not delete the task. The oscillator re-attaches, holds PWM, and `Home()` returns to 90 degrees. A later Mickey namespace would be `self.mickey.*`. User firmware upgrade is `self.upgrade_firmware`. See [[No Hands Four Servos]].
