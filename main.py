from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

from config import get_settings
from tools import TOOLS

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

mcp.add_middleware(RateLimitingMiddleware(
    max_requests_per_second=settings.rate_limit_rps,
    burst_capacity=settings.rate_limit_burst,
    get_client_id=lambda context: (
        str(token.claims.get("id") or token.client_id)
        if (token := get_access_token()) else "anonymous"
    ),
))

for tool in TOOLS:
    mcp.add_tool(tool)


if __name__ == "__main__":
    mcp.run(
        host=settings.host,
        port=settings.port,
        transport="http",
    )
