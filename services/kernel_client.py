# Runs inside the microVM: executes stdin as one cell in the session's kernel.

from pathlib import Path
from queue import Empty
from sys import argv, exit, stderr, stdin, stdout
from time import sleep

from jupyter_client import BlockingKernelClient  # pyright: ignore[reportMissingImports]

connection_file, kernel_pid = argv[1:]
client = BlockingKernelClient()

# A starting kernel has not written its connection file yet.
while True:
    try:
        client.load_connection_file(connection_file)
        break
    except (OSError, ValueError):
        sleep(0.05)

# A busy kernel misses heartbeats; /proc shows whether it lives.
client.start_channels(hb=False)
client.wait_for_ready()

message_id = client.execute(stdin.read(), allow_stdin=False)
failed = False

while True:

    try:
        message = client.get_iopub_msg(timeout=1)
    except Empty:
        if not Path(f"/proc/{kernel_pid}").exists():
            exit("The kernel died, e.g. out of memory; variables are lost.")
        continue

    if message["parent_header"].get("msg_id") != message_id:
        continue

    message_type = message["msg_type"]
    content = message["content"]

    if message_type == "stream":
        stream = stderr if content["name"] == "stderr" else stdout
        stream.write(content["text"])
    elif message_type in ("execute_result", "display_data"):
        print(content["data"].get("text/plain", ""))
    elif message_type == "error":
        failed = True
        print("\n".join(content["traceback"]), file=stderr)
    elif message_type == "status" and content["execution_state"] == "idle":
        break

exit(failed)
