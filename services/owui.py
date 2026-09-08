from httpx2 import AsyncClient, HTTPStatusError, RequestError

from config import get_settings

_settings = get_settings()

_REQUEST_TIMEOUT_SECONDS = 60.0

_FILE_UPLOAD_URL = "{base_url}/api/v1/files/"
_FILE_DOWNLOAD_URL = "{base_url}/api/v1/files/{file_id}/content"


def _client() -> AsyncClient:
    return AsyncClient(
        verify=_settings.owui_verify_tls,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )


async def download_file(
    file_id: str,
    token: str,
    max_bytes: int,
) -> bytes:

    try:
        async with (
            _client() as client,
            client.stream(
                "GET",
                url=_FILE_DOWNLOAD_URL.format(
                    base_url=_settings.owui_base_url,
                    file_id=file_id,
                ),
                headers={"Authorization": f"Bearer {token}"},
            ) as response,
        ):
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
            "OpenWebUI rejected the download."
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
) -> str:

    try:
        async with _client() as client:
            response = await client.post(
                url=_FILE_UPLOAD_URL.format(
                    base_url=_settings.owui_base_url,
                ),
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (file_name, data, content_type)},
            )
            response.raise_for_status()

        file_id = response.json().get("id")

        if not file_id:
            raise RuntimeError(
                "OpenWebUI upload response carries no file id.")

        return _FILE_DOWNLOAD_URL.format(
            base_url=_settings.owui_base_url,
            file_id=file_id,
        )

    except HTTPStatusError as error:
        raise RuntimeError(
            "OpenWebUI rejected the upload."
        ) from error
    except RequestError as error:
        raise RuntimeError(
            "Could not reach OpenWebUI."
        ) from error
