from pydantic import BaseModel


class HealthData(BaseModel):
    status: str


class HealthResponse(BaseModel):
    request_id: str
    data: HealthData
