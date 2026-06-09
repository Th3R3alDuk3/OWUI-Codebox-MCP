from pydantic import BaseModel, ConfigDict


class OWUIFile(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    filename: str = ""
