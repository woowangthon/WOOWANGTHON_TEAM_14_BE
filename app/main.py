import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.routers import sessions  # noqa: E402  (.env를 먼저 읽는다)

app = FastAPI(title="빠진 페이지 BE")

_origins = list(dict.fromkeys(
    o for o in [(os.getenv("FE_ORIGIN") or "").strip(), "http://localhost:5173"] if o
))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 에러는 항상 {"error": {"code", "message"}} 형태. 세션 없음(404)만 예외.
@app.exception_handler(RequestValidationError)
def _invalid_request(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []))
    return JSONResponse(
        status_code=200,
        content={"error": {"code": "invalid_request", "message": f"{where}: {first.get('msg', '요청 형식 오류')}"}},
    )


@app.exception_handler(Exception)
def _unexpected(request: Request, exc: Exception):
    return JSONResponse(
        status_code=200,
        content={"error": {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"[:300]}},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(sessions.router)
