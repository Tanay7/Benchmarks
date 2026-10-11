# DSS-Q Station I/O - App Lab entry point (VENTUNO Q).
#
# Everything this App does happens in sketch/ on the STM32H5: CardKB keyboards,
# annunciator LEDs / buzzer and the Radio Control Unit. The ground data system
# (python -m dssq.gds, ground/README in the repository) runs as a normal Linux
# service on the same board and talks to the sketch through arduino-router, so
# this Python side only reports that the App is up.
from arduino.app_utils import App

print("DSS-Q station I/O sketch flashed. Start the GDS on the board: "
      "systemctl start dssq-gds  (or: cd ground && .venv/bin/python -m dssq.gds)", flush=True)
App.run()
