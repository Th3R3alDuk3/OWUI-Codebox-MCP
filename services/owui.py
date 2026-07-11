from httpx import AsyncClient, HTTPStatusError, RequestError

from config import get_settings

_settings = get_settings()

_REQUEST_TIMEOUT_SECONDS = 30.0

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
) -> bytes:

    try:
        async with _client() as client:
            response = await client.get(
                url=_FILE_DOWNLOAD_URL.format(
                    base_url=_settings.owui_base_url,
                    file_id=file_id,
                ),
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

        return response.content

    except HTTPStatusError as error:
        raise RuntimeError(
            f"OpenWebUI rejected the download. Detail: {error}"
        ) from error
    except RequestError as error:
        raise RuntimeError(
            f"Could not reach OpenWebUI. Detail: {error}"
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
            f"OpenWebUI rejected the upload. Detail: {error}"
        ) from error
    except RequestError as error:
        raise RuntimeError(
            f"Could not reach OpenWebUI. Detail: {error}"
        ) from error
