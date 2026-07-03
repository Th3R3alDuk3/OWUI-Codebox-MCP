from pydantic import BaseModel, ConfigDict, Field


class OWUIFile(BaseModel):
    model_config = ConfigDict(
        extra="allow", 
        validate_by_name=True,
    )

    id: str
    file_name: str = Field(default="", alias="filename")
