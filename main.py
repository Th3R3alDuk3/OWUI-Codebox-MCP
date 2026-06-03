from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier

from config import get_settings
from subservers.codebox.server import lifespan as codebox_lifespan
from subservers.codebox.server import mcp as codebox_mcp


settings = get_settings()

ROOT_INSTRUCTIONS = """
OWUI-Codebox-MCP gives each user a private, isolated Python sandbox running in
its own container (powered by llm-sandbox).

Every user has exactly one sandbox session, identified by their OpenWebUI user
id. The session is a live IPython kernel: variables, imports, functions and
files persist across calls, so you can build up a computation step by step.

Tools (prefixed with `py_`):
- `py_run_code` executes Python in the user's session and returns stdout,
  stderr and the exit code. The session is created automatically on first
  use. Pass `libraries` to pip-install packages (e.g. ['numpy', 'pandas'])
  before the code runs.
- `py_reset_session` throws away all variables and files and starts fresh.
- `py_stop_session` tears the sandbox down completely.
- `py_session_info` inspects the current session (age, idle time, backend).
- `py_attach_file` pulls a file the user attached in OpenWebUI into the
  sandbox working directory so code can read it.
- `py_save_file` takes a file the sandbox produced and uploads it back to
  OpenWebUI so the user can download it.

Workflow: just call `py_run_code` with the code. Persist intermediate results
in variables instead of recomputing them. Only `py_reset_session` clears
state. To work with user files, `py_attach_file` first, then read the path
from your code; to hand a result back, write it to a file and call
`py_save_file`.
""".strip()

auth = JWTVerifier(
    public_key=settings.jwt_secret,
    algorithm=settings.jwt_algorithm,
)

mcp = FastMCP(
    name="OWUI-Codebox-MCP",
    instructions=ROOT_INSTRUCTIONS,
    auth=auth,
    lifespan=codebox_lifespan,
)

mcp.mount(codebox_mcp, namespace="py")


if __name__ == "__main__":
    mcp.run(
        host=settings.host, port=settings.port,
        transport="streamable-http",
    )
