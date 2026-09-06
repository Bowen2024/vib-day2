from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from api.asr import router as asr_router
from api.health import router as health_router
from api.upload import router as upload_router
from config import settings
from errors import AppError
from schemas import ErrorBody, ErrorResponse

app = FastAPI(title="语音约碰面地点", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(upload_router)
app.include_router(asr_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request.state.request_id = str(uuid4())
    return await call_next(request)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    body = ErrorResponse(
        request_id=_request_id(request),
        error=ErrorBody(code=exc.code, message=exc.message, stage=exc.stage),
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    path = request.url.path
    if path == "/upload":
        stage = "upload"
    elif path == "/asr":
        stage = "asr"
    else:
        stage = "request"
    body = ErrorResponse(
        request_id=_request_id(request),
        error=ErrorBody(
            code="VALIDATION_ERROR",
            message="请求缺少文件或字段类型不正确。",
            stage=stage,
        ),
    )
    return JSONResponse(status_code=422, content=body.model_dump())
