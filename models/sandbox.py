from pydantic import BaseModel, Field


class InputFile(BaseModel):
    id: str = Field(
        description="OpenWebUI ID of a file the user attached.",
    )
    path: str = Field(
        description=(
            "Absolute sandbox path to place the file at "
            "(e.g. '/sandbox/data.csv')."
        ),
    )


class OutputFile(BaseModel):
    name: str = Field(
        description="Name of the file returned from the sandbox.",
    )
    size: int = Field(
        description="Size of the returned file in bytes.",
    )
    download_url: str = Field(
        description="Download link — give this URL to the user.",
    )


class ExecResult(BaseModel):
    exit_code: int = Field(
        description="Exit code; 0 means the script ran successfully.",
    )
    stdout: str = Field(
        description="Standard output.",
    )
    stderr: str = Field(
        description="Standard error output, including the traceback on failure.",
    )
    output_files: list[OutputFile] = Field(
        description=(
            "Files returned to the user; empty unless `output_files` was set "
            "and the run succeeded."
        ),
    )


class InstalledPackage(BaseModel):
    name: str = Field(
        description="Package name as used with pip.",
    )
    version: str = Field(
        description="Installed version.",
    )


class PackageListing(BaseModel):
    image: str = Field(
        description="Sandbox image the listing was taken from.",
    )
    packages: list[InstalledPackage] = Field(
        description="Python packages preinstalled in the sandbox image.",
    )
