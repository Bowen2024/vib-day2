from pydantic import BaseModel


class HealthData(BaseModel):
    status: str


class HealthResponse(BaseModel):
    request_id: str
    data: HealthData


class UploadData(BaseModel):
    audio_id: str


class UploadResponse(BaseModel):
    request_id: str
    data: UploadData


class AsrRequest(BaseModel):
    audio_id: str


class AsrData(BaseModel):
    text: str


class AsrResponse(BaseModel):
    request_id: str
    data: AsrData


class ErrorBody(BaseModel):
    code: str
    message: str
    stage: str


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorBody
