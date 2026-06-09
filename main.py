from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier

from config import get_settings
from subservers.codebox.server import mcp as codebox_mcp


settings = get_settings()

ROOT_INSTRUCTIONS = """
OWUI-Codebox-MCP runs Python in a disposable, isolated container (powered by
llm-sandbox): every call gets a fresh sandbox that is torn down right after, so
nothing persists between calls. Its single tool, `run_python`, documents the
full usage — including how to pass files in and out.
""".strip()

auth = JWTVerifier(
    public_key=settings.jwt_secret,
    algorithm=settings.jwt_algorithm,
)

mcp = FastMCP(
    name="OWUI-Codebox-MCP",
    instructions=ROOT_INSTRUCTIONS,
    auth=auth,
)

mcp.mount(codebox_mcp)


if __name__ == "__main__":
    mcp.run(
        host=settings.host, port=settings.port,
        transport="streamable-http",
    )
