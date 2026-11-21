import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "w") as handle:
    handle.write("ready")
while True:
    time.sleep(1)
