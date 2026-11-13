from httpx2 import AsyncClient, HTTPStatusError, RequestError, Response

from config import get_settings

_settings = get_settings()

_REQUEST_TIMEOUT_SECONDS = 60.0

# OpenWebUI's API takes only its own tokens.
_TOKEN_EXCHANGE_PATH = "/api/v1/auths/oauth/oidc/token/exchange"
_FILE_DOWNLOAD_PATH = "/api/v1/files/{file_id}/content"
# Output files are only downloaded, so OpenWebUI skips extraction and embedding.
_FILE_UPLOAD_PATH = "/api/v1/files/?process=false"

# Shared, so calls reuse connections.
_client = AsyncClient(
    base_url=_settings.owui_base_url,
    timeout=_REQUEST_TIMEOUT_SECONDS,
)


def _error_detail(
    response: Response,
) -> str:

    try:
        return str(response.json()["detail"]).rstrip(".")
    except (ValueError, KeyError, TypeError):
        return f"{response.status_code} {response.reason_phrase}"


async def exchange_token(
    token: str,
) -> str:

    try:
        response = await _client.post(
            _TOKEN_EXCHANGE_PATH,
            json={"token": token},
        )
        response.raise_for_status()
    except HTTPStatusError as error:
        raise RuntimeError(
            "OpenWebUI rejected the token exchange: "
            f"{_error_detail(error.response)}."
        ) from error
    except RequestError as error:
        raise RuntimeError(
            "Could not reach OpenWebUI."
        ) from error

    owui_token = response.json().get("token")

    if not owui_token:
        raise RuntimeError(
            "OpenWebUI answered the token exchange without a token.")

    return owui_token


async def download_file(
    file_id: str,
    owui_token: str,
    max_size: int,
) -> bytes:

    try:
        async with _client.stream(
            "GET",
            _FILE_DOWNLOAD_PATH.format(file_id=file_id),
            headers={"Authorization": f"Bearer {owui_token}"},
        ) as response:
            if response.is_error:
                await response.aread()

            response.raise_for_status()

            data = bytearray()

            async for chunk in response.aiter_bytes():
                data += chunk
                # One byte over is enough for the caller to reject it.
                if len(data) > max_size:
                    break
    except HTTPStatusError as error:
        raise RuntimeError(
            f"OpenWebUI rejected the download: {_error_detail(error.response)}."
        ) from error
    except RequestError as error:
        raise RuntimeError(
            "Could not reach OpenWebUI."
        ) from error

    return bytes(data)


async def upload_file(
    file_name: str,
    data: bytes,
    owui_token: str,
) -> tuple[str, str]:

    try:
        response = await _client.post(
            _FILE_UPLOAD_PATH,
            headers={"Authorization": f"Bearer {owui_token}"},
            files={"file": (file_name, data)},
        )
        response.raise_for_status()
    except HTTPStatusError as error:
        raise RuntimeError(
            f"OpenWebUI rejected the upload: {_error_detail(error.response)}."
        ) from error
    except RequestError as error:
        raise RuntimeError(
            "Could not reach OpenWebUI."
        ) from error

    file_id = response.json().get("id")

    if not file_id:
        raise RuntimeError(
            "OpenWebUI answered the upload without a file ID.")

    # Relative to OpenWebUI.
    return file_id, _FILE_DOWNLOAD_PATH.format(file_id=file_id)
