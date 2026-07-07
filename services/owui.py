from httpx import AsyncClient, HTTPStatusError, RequestError

from config import get_settings
from models.owui import OWUIFile

_settings = get_settings()


REQUEST_TIMEOUT_SECONDS = 30.0
FILE_UPLOAD_URL = "{base_url}/api/v1/files/"
FILE_DOWNLOAD_URL = "{base_url}/api/v1/files/{file_id}/content"


def _client() -> AsyncClient:
    return AsyncClient(
        verify=_settings.owui_verify_tls,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


async def upload_file(
    file_name: str,
    data: bytes,
    content_type: str,
    token: str,
) -> OWUIFile:

    try:

        async with _client() as client:
            response = await client.post(
                url=FILE_UPLOAD_URL.format(
                    base_url=_settings.owui_base_url),
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (file_name, data, content_type)},
            )

        response.raise_for_status()

        owui_file = OWUIFile.model_validate(response.json())
        owui_file.download_url = FILE_DOWNLOAD_URL.format(
            base_url=_settings.owui_base_url, file_id=owui_file.id)

        return owui_file

    except HTTPStatusError as error:
        raise RuntimeError(
            f"OpenWebUI rejected the upload. Detail: {error}"
        ) from error
    except RequestError as error:
        raise RuntimeError(
            f"Could not reach OpenWebUI. Detail: {error}"
        ) from error


async def download_file(
    file_id: str,
    token: str,
) -> bytes:

    try:

        async with _client() as client:

            content_response = await client.get(
                url=FILE_DOWNLOAD_URL.format(
                    base_url=_settings.owui_base_url, file_id=file_id),
                headers={"Authorization": f"Bearer {token}"},
            )
            content_response.raise_for_status()

        return content_response.content

    except HTTPStatusError as error:
        raise RuntimeError(
            f"OpenWebUI rejected the download. Detail: {error}"
        ) from error
    except RequestError as error:
        raise RuntimeError(
            f"Could not reach OpenWebUI. Detail: {error}"
        ) from error
