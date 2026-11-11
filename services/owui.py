from httpx2 import AsyncClient, HTTPStatusError, RequestError, Response

from config import get_settings

_settings = get_settings()

_REQUEST_TIMEOUT_SECONDS = 60.0

# OpenWebUI's API takes only its own tokens.
_TOKEN_EXCHANGE_URL = "{base_url}/api/v1/auths/oauth/oidc/token/exchange"
# Output files are only downloaded, so OpenWebUI skips extraction and embedding.
_FILE_UPLOAD_URL = "{base_url}/api/v1/files/?process=false"
_FILE_DOWNLOAD_URL = "{base_url}/api/v1/files/{file_id}/content"


def _client() -> AsyncClient:
    return AsyncClient(
        verify=_settings.owui_verify_tls,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )


def _error_detail(
    response: Response,
) -> str:
    try:
        return str(response.json()["detail"]).rstrip(".")
    except (ValueError, KeyError, TypeError):
        return f"{response.status_code} {response.reason_phrase}"


async def _exchange_token(
    token: str,
) -> str:

    try:
        async with _client() as client:
            response = await client.post(
                url=_TOKEN_EXCHANGE_URL.format(
                    base_url=_settings.owui_base_url,
                ),
                json={"token": token},
            )
            response.raise_for_status()

    except HTTPStatusError as error:
        raise RuntimeError(
            "OpenWebUI rejected the token exchange: "
            f"{_error_detail(error.response)}."
        ) from error

    owui_token = response.json().get("token")

    if not owui_token:
        raise RuntimeError(
            "OpenWebUI answered the token exchange without a token.")

    return owui_token


async def download_file(
    file_id: str,
    token: str,
    max_bytes: int,
) -> bytes:

    try:
        owui_token = await _exchange_token(token)

        async with (
            _client() as client,
            client.stream(
                "GET",
                url=_FILE_DOWNLOAD_URL.format(
                    base_url=_settings.owui_base_url,
                    file_id=file_id,
                ),
                headers={"Authorization": f"Bearer {owui_token}"},
            ) as response,
        ):
            if response.is_error:
                await response.aread()

            response.raise_for_status()

            data = bytearray()

            async for chunk in response.aiter_bytes():
                data += chunk
                # One byte over is enough for the caller to reject it.
                if len(data) > max_bytes:
                    break

        return bytes(data)

    except HTTPStatusError as error:
        raise RuntimeError(
            f"OpenWebUI rejected the download: {_error_detail(error.response)}."
        ) from error
    except RequestError as error:
        raise RuntimeError(
            "Could not reach OpenWebUI."
        ) from error


async def upload_file(
    file_name: str,
    data: bytes,
    content_type: str,
    token: str,
) -> tuple[str, str]:

    try:
        owui_token = await _exchange_token(token)

        async with _client() as client:
            response = await client.post(
                url=_FILE_UPLOAD_URL.format(
                    base_url=_settings.owui_base_url,
                ),
                headers={"Authorization": f"Bearer {owui_token}"},
                files={"file": (file_name, data, content_type)},
            )
            response.raise_for_status()

        file_id = response.json().get("id")

        if not file_id:
            raise RuntimeError(
                "OpenWebUI upload response carries no file id.")

        # Relative to OpenWebUI.
        return file_id, _FILE_DOWNLOAD_URL.format(
            base_url="",
            file_id=file_id,
        )

    except HTTPStatusError as error:
        raise RuntimeError(
            f"OpenWebUI rejected the upload: {_error_detail(error.response)}."
        ) from error
    except RequestError as error:
        raise RuntimeError(
            "Could not reach OpenWebUI."
        ) from error
