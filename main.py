from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

from config import get_settings
from tools import TOOLS

settings = get_settings()

INSTRUCTIONS = """
OWUI-Codebox-MCP runs Python in disposable, isolated containers (powered by
llm-sandbox): every call gets a fresh sandbox that is torn down right after, so
nothing persists between calls. Call `list_python_packages` first to see which
packages are preinstalled in the sandbox image and prefer those; `run_python`
documents its full usage — including how to pass files in and out.
""".strip()

mcp = FastMCP(
    name="OWUI-Codebox-MCP",
    instructions=INSTRUCTIONS,
    auth=JWTVerifier(
        public_key=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    ),
    middleware=[
        RateLimitingMiddleware(
            max_requests_per_second=settings.rate_limit_rps,
            burst_capacity=settings.rate_limit_burst,
            # OpenWebUI JWTs carry the user in the `id` claim.
            get_client_id=lambda context: (
                token.claims.get("id", "anonymous")
                if (token := get_access_token()) else "anonymous"
            ),
        ),
    ],
    tools=TOOLS,
    mask_error_details=True,
)


if __name__ == "__main__":
    mcp.run(
        host="0.0.0.0",
        port=8000,
        transport="http",
    )
