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
        description=(
            "Direct OpenWebUI download link for the file. Give this URL to the "
            "user so they can download the file the code produced."
        ),
    )


class ExecResult(BaseModel):
    exit_code: int = Field(
        description="Process exit code; 0 means the script ran successfully.",
    )
    stdout: str = Field(
        default="",
        description="Everything the script printed to standard output.",
    )
    stderr: str = Field(
        default="",
        description="Standard error output, including the traceback on failure.",
    )
    output_files: list[OutputFile] = Field(
        default_factory=list,
        description=(
            "Files returned to the user, present only when `output_files` "
            "was set on the call and the run succeeded; otherwise empty."
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
