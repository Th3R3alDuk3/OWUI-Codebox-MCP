from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

from config import get_settings
from services.session import session_lifespan
from tools import TOOLS

settings = get_settings()

INSTRUCTIONS = "Runs Python in isolated microVMs."

mcp = FastMCP(
    name="OWUI-Codebox-MCP",
    instructions=INSTRUCTIONS,
    auth=JWTVerifier(
        jwks_uri=settings.jwt_jwks_uri,
        issuer=settings.jwt_issuer,
        algorithm="RS256",
    ),
    middleware=[
        RateLimitingMiddleware(
            max_requests_per_second=settings.rate_limit_rps,
            burst_capacity=settings.rate_limit_burst,
            # Keycloak access tokens carry the user in the `sub` claim.
            get_client_id=lambda context: (
                token.claims.get("sub", "anonymous")
                if (token := get_access_token()) else "anonymous"
            ),
        ),
    ],
    lifespan=session_lifespan,
    tools=TOOLS,
    mask_error_details=True,
)


if __name__ == "__main__":
    mcp.run(
        host="0.0.0.0",
        port=8000,
        transport="http",
        stateless_http=True,
        # The banner checks PyPI for FastMCP updates.
        show_banner=False,
    )
