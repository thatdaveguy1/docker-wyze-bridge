from pydantic import BaseModel, ConfigDict, Field


class IceServer(BaseModel):
    url: str
    username: str = ""
    credential: str = ""


class ParamsBean(BaseModel):
    signaling_url: str = ""
    auth_token: str = ""
    ice_servers: list[IceServer] = Field(default_factory=list)


class PropertyBean(BaseModel):
    property_data: dict[str, int] = Field(default_factory=dict, alias="property")


class Stream(BaseModel):
    property: PropertyBean
    device_id: str
    provider: str
    params: ParamsBean


class WpkStreamInfo(BaseModel):
    code: str
    ts: int
    msg: str
    data: list[Stream]
    traceId: str | None = None

    model_config = ConfigDict(populate_by_name=True)
