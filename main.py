from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

from config import get_settings
from tools import TOOLS

settings = get_settings()

ROOT_INSTRUCTIONS = """
OWUI-Codebox-MCP runs code in disposable, isolated containers (powered by
llm-sandbox): every call gets a fresh sandbox that is torn down right after, so
nothing persists between calls. Tools are grouped per language. Each `run_*`
tool documents its full usage — including how to pass files in and out; the
`list_*_packages` tools show which packages are preinstalled in the sandbox
image.
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
    # OpenWebUI JWTs carry the user in the `id` claim.
    get_client_id=lambda context: (
        token.claims.get("id", "anonymous")
        if (token := get_access_token()) else "anonymous"
    ),
))

for tool in TOOLS:
    mcp.add_tool(tool)


if __name__ == "__main__":
    mcp.run(
        host="0.0.0.0",
        port=8000,
        transport="http",
    )
