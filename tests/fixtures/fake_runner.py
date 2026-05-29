"""Stand-in for `modal run <config>` used in tests.

Reads behavior from env vars so a single fixture can simulate
many outcomes:
  FAKE_EXIT_CODE  - exit with this code (default 0)
  FAKE_SLEEP      - seconds to sleep before exiting (default 0)
  FAKE_STDERR     - message to write to stderr (optional)

Always echoes the MODAL_TOKEN_ID and MODAL_TOKEN_SECRET it received
so tests can confirm env propagation.
"""
import os
import sys
import time

exit_code = int(os.environ.get("FAKE_EXIT_CODE", "0"))
sleep_seconds = float(os.environ.get("FAKE_SLEEP", "0"))
stderr_msg = os.environ.get("FAKE_STDERR")

print(f"fake_runner args={sys.argv[1:]}", flush=True)
print(f"MODAL_TOKEN_ID={os.environ.get('MODAL_TOKEN_ID', '<unset>')}", flush=True)
print(f"MODAL_TOKEN_SECRET={os.environ.get('MODAL_TOKEN_SECRET', '<unset>')}", flush=True)

if stderr_msg:
    print(stderr_msg, file=sys.stderr)
if sleep_seconds > 0:
    time.sleep(sleep_seconds)

sys.exit(exit_code)
