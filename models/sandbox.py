from pydantic import BaseModel, Field


class OutputFile(BaseModel):
    file_name: str = Field(
        description="Name of the file returned from the sandbox.",
    )
    file_size: int = Field(
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
    duration_ms: int = Field(
        default=0,
        description="Wall-clock execution time of the script in milliseconds.",
    )
    output_file: OutputFile | None = Field(
        default=None,
        description=(
            "The file returned to the user, present only when `output_file_path` "
            "was set and the run succeeded; otherwise null."
        ),
    )
