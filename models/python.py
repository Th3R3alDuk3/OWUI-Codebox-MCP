from pydantic import BaseModel, Field


class InstalledPackage(BaseModel):
    name: str = Field(
        description="Package name as used in `packages`.",
    )
    version: str = Field(
        description="Installed version.",
    )


class PackageList(BaseModel):
    packages: list[InstalledPackage] = Field(
        description="Packages preinstalled in the sandbox image.",
    )


class InputFile(BaseModel):
    id: str = Field(
        description="OpenWebUI ID of a file the user attached.",
    )
    path: str = Field(
        description="Path under /sandbox to place the file at, e.g. '/sandbox/data.csv'.",
    )


class OutputFile(BaseModel):
    name: str = Field(
        description="Name of the file returned from the sandbox.",
    )
    size: int = Field(
        description="Size in bytes.",
    )
    download_url: str = Field(
        description="Download URL to give to the user.",
    )


class RunResult(BaseModel):
    exit_code: int = Field(
        description=(
            "Exit code; 0 means success, 1 an error, -1 that the run printed "
            "too much and the kernel restarted, losing its variables."
        ),
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
