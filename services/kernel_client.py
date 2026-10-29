"""Executes stdin as one cell in the session's Jupyter kernel, inside the microVM."""

import os
import sys
import time
from queue import Empty

from jupyter_client import BlockingKernelClient  # pyright: ignore[reportMissingImports]

connection_file, kernel_pid = sys.argv[1:]
client = BlockingKernelClient()

# A starting kernel has not written its connection file yet.
while True:
    try:
        client.load_connection_file(connection_file)
        break
    except (OSError, ValueError):
        time.sleep(0.05)

# A busy kernel misses heartbeats; /proc shows whether it lives.
client.start_channels(hb=False)
client.wait_for_ready()
msg_id = client.execute(sys.stdin.read(), allow_stdin=False)
failed = False

while True:

    try:
        message = client.get_iopub_msg(timeout=1)
    except Empty:
        if not os.path.exists(f"/proc/{kernel_pid}"):
            sys.exit("The kernel died, e.g. out of memory; variables are lost.")
        continue

    if message["parent_header"].get("msg_id") != msg_id:
        continue

    content = message["content"]

    match message["msg_type"]:
        case "stream":
            getattr(sys, content["name"]).write(content["text"])
        case "execute_result" | "display_data":
            print(content["data"].get("text/plain", ""))
        case "error":
            failed = True
            print("\n".join(content["traceback"]), file=sys.stderr)
        case "status" if content["execution_state"] == "idle":
            break

sys.exit(failed)
