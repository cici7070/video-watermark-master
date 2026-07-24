from contextlib import asynccontextmanager
from dataclasses import replace
from ipaddress import ip_address
from pathlib import Path
import asyncio
import base64
import os

import cv2
from fastapi import FastAPI, File, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from watermark_master.detector import detect_candidates
from watermark_master.errors import UserFacingError
from watermark_master.models import Box
from watermark_master.video import ALLOWED_SUFFIXES, probe_video, sample_frames
from watermark_master.web_jobs import JobManager, JobState
from watermark_master.web_limits import (
    RollingRateLimiter,
    WebLimits,
    WebValidationError,
    validate_public_video,
)
from watermark_master.workspace import JobWorkspace


class BoxInput(BaseModel):
    x: int
    y: int
    width: int
    height: int


class ProcessInput(BaseModel):
    boxes: list[BoxInput]
    rights_confirmed: bool


def error(code: str, message: str, status: int, **headers) -> JSONResponse:
    """回傳前端可一致處理的 API 錯誤格式。"""
    response_headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'self'; img-src 'self' blob: data:; "
            "media-src 'self' blob:; style-src 'self'; script-src 'self'"
        ),
    }
    response_headers.update(
        {key.replace("_", "-"): str(value) for key, value in headers.items()}
    )
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers=response_headers,
    )


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """在 multipart 解析前限制整個 HTTP request body。"""

    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                declared_size = 0
            if declared_size > self.max_body_bytes:
                await error(
                    "FILE_TOO_LARGE",
                    "影片檔案過大，請壓縮後再上傳。",
                    413,
                )(scope, receive, send)
                return

        received = 0
        exceeded = False
        downstream_sent = False

        async def limited_receive():
            nonlocal exceeded, received
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message):
            nonlocal downstream_sent
            if exceeded:
                return
            downstream_sent = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not exceeded:
                raise

        if exceeded and not downstream_sent:
            await error(
                "FILE_TOO_LARGE",
                "影片檔案過大，請壓縮後再上傳。",
                413,
            )(scope, receive, send)


def parse_trusted_proxy_ips(value: str) -> frozenset[str]:
    trusted = set()
    for item in value.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            trusted.add(str(ip_address(candidate)))
        except ValueError:
            continue
    return frozenset(trusted)


def client_ip(
    request: Request,
    trusted_proxies: frozenset[str] = frozenset(),
) -> str:
    """在反向代理與本機開發環境都能取得限流用 IP。"""
    peer = request.client.host if request.client else "unknown"
    try:
        normalized_peer = str(ip_address(peer))
    except ValueError:
        normalized_peer = peer

    if normalized_peer not in trusted_proxies:
        return normalized_peer

    forwarded = request.headers.get("x-forwarded-for", "")
    for item in reversed(forwarded.split(",")):
        candidate = item.strip()
        try:
            normalized_candidate = str(ip_address(candidate))
        except ValueError:
            return normalized_peer
        if normalized_candidate in trusted_proxies:
            continue
        return normalized_candidate
    return normalized_peer


def token_value(token: str | None) -> str:
    if not token:
        raise PermissionError
    return token


def create_app(root: Path | None = None) -> FastAPI:
    runtime_root = Path(root or os.getenv("WEB_RUNTIME_ROOT", ".web-runtime")).resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    manager = JobManager(
        runtime_root,
        ttl_seconds=int(os.getenv("JOB_TTL_SECONDS", "1800")),
        max_queue=int(os.getenv("MAX_QUEUE_SIZE", "5")),
    )
    limiter = RollingRateLimiter(limit=int(os.getenv("IP_JOB_LIMIT", "3")))
    limits = WebLimits(
        max_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024))),
        max_width=int(os.getenv("MAX_VIDEO_WIDTH", "1920")),
        max_height=int(os.getenv("MAX_VIDEO_HEIGHT", "1080")),
        max_duration_seconds=float(os.getenv("MAX_VIDEO_SECONDS", "60")),
    )
    multipart_overhead = max(
        0,
        int(os.getenv("MULTIPART_OVERHEAD_BYTES", str(64 * 1024))),
    )
    max_request_bytes = limits.max_bytes + multipart_overhead
    trusted_proxies = parse_trusted_proxy_ips(
        os.getenv("TRUSTED_PROXY_IPS", "")
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.jobs = manager
        application.state.limiter = limiter
        stop = asyncio.Event()

        async def cleanup_loop() -> None:
            while not stop.is_set():
                manager.cleanup_expired()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                except TimeoutError:
                    pass

        cleanup_task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            stop.set()
            await cleanup_task
            manager.close()

    application = FastAPI(title="影片去浮水印大師", lifespan=lifespan)

    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=max_request_bytes,
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; "
            "media-src 'self' blob:; style-src 'self'; script-src 'self'"
        )
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exception: RequestValidationError):
        return error("INVALID_REQUEST", "資料格式不正確，請重新操作。", 422)

    @application.exception_handler(RequestBodyTooLarge)
    async def request_too_large(_request: Request, _exception: RequestBodyTooLarge):
        return error(
            "FILE_TOO_LARGE",
            "影片檔案過大，請壓縮後再上傳。",
            413,
        )

    @application.exception_handler(WebValidationError)
    async def web_validation_error(_request: Request, exception: WebValidationError):
        return error(exception.code, str(exception), 422)

    @application.exception_handler(UserFacingError)
    async def user_error(_request: Request, exception: UserFacingError):
        return error("UNSUPPORTED_VIDEO", str(exception), 422)

    @application.exception_handler(Exception)
    async def unexpected_error(_request: Request, _exception: Exception):
        return error(
            "INTERNAL_ERROR",
            "系統暫時發生問題，請稍後再試。",
            500,
        )

    def require(job_id: str, token: str | None):
        try:
            return manager.require(job_id, token_value(token))
        except PermissionError:
            raise
        except KeyError:
            return None

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.post("/api/jobs", status_code=201)
    async def create_job(file: UploadFile = File(...)):
        suffix = Path(file.filename or "").suffix.lower()
        workspace: JobWorkspace | None = None
        size = 0
        try:
            if suffix not in ALLOWED_SUFFIXES:
                raise WebValidationError(
                    "UNSUPPORTED_VIDEO",
                    "僅支援 MP4、MOV 或 AVI 影片。",
                )
            workspace = JobWorkspace.create(runtime_root)
            destination = workspace.path / f"source{suffix}"
            with destination.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limits.max_bytes:
                        raise WebValidationError(
                            "FILE_TOO_LARGE",
                            "影片不可超過 200 MB，請壓縮後再上傳。",
                        )
                    output.write(chunk)

            info = replace(probe_video(destination), path=destination)
            validate_public_video(info, size, limits)
            frames = sample_frames(info, count=12, max_width=640)
            ok, encoded = cv2.imencode(
                ".jpg", frames[0], [cv2.IMWRITE_JPEG_QUALITY, 82]
            )
            if not ok:
                raise UserFacingError("無法建立影片預覽，請更換影片後再試。")

            record = manager.create(workspace, info)
            preview = "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
            return {
                "job_id": record.job_id,
                "job_token": record.token,
                "preview": preview,
                "video": {
                    "width": info.width,
                    "height": info.height,
                    "duration_seconds": round(info.duration_seconds, 2),
                    "has_audio": info.has_audio,
                },
            }
        except Exception:
            if workspace is not None:
                workspace.cleanup()
            raise
        finally:
            await file.close()

    @application.post("/api/jobs/{job_id}/detect")
    def detect(job_id: str, x_job_token: str | None = Header(default=None)):
        try:
            record = require(job_id, x_job_token)
        except PermissionError:
            return error("INVALID_JOB_TOKEN", "工作驗證失敗，請重新上傳影片。", 403)
        if record is None:
            return error("JOB_EXPIRED", "工作已過期，請重新上傳影片。", 404)

        frames = sample_frames(record.info, count=12, max_width=640)
        candidates = detect_candidates(
            frames, (record.info.width, record.info.height), limit=3
        )
        return {
            "boxes": [
                {
                    "x": item.box.x,
                    "y": item.box.y,
                    "width": item.box.width,
                    "height": item.box.height,
                    "confidence": round(item.confidence, 2),
                }
                for item in candidates
            ]
        }

    @application.post("/api/jobs/{job_id}/process", status_code=202)
    def process(
        job_id: str,
        payload: ProcessInput,
        request: Request,
        x_job_token: str | None = Header(default=None),
    ):
        try:
            record = require(job_id, x_job_token)
        except PermissionError:
            return error("INVALID_JOB_TOKEN", "工作驗證失敗，請重新上傳影片。", 403)
        if record is None:
            return error("JOB_EXPIRED", "工作已過期，請重新上傳影片。", 404)
        if not payload.rights_confirmed:
            return error("RIGHTS_NOT_CONFIRMED", "請先確認你有權處理這段影片。", 422)

        ip = client_ip(request, trusted_proxies)
        reservation = limiter.consume(ip)
        if not reservation.allowed:
            return error(
                "RATE_LIMITED",
                "24 小時內的 3 次額度已用完，請稍後再試。",
                429,
                Retry_After=reservation.retry_after_seconds,
            )
        try:
            manager.submit(
                job_id,
                record.token,
                [Box(item.x, item.y, item.width, item.height) for item in payload.boxes],
            )
        except KeyError:
            limiter.rollback(ip, reservation.reservation_id)
            return error("JOB_EXPIRED", "工作已過期，請重新上傳影片。", 404)
        except ValueError:
            limiter.rollback(ip, reservation.reservation_id)
            return error(
                "INVALID_BOXES",
                "選取範圍不正確，請重新選取。",
                422,
            )
        except RuntimeError:
            limiter.rollback(ip, reservation.reservation_id)
            return error(
                "JOB_ALREADY_STARTED",
                "這項工作已開始處理，請勿重複送出。",
                409,
            )
        except OverflowError:
            limiter.rollback(ip, reservation.reservation_id)
            return error(
                "QUEUE_FULL",
                "目前處理人數較多，請稍後再試。",
                503,
            )
        except Exception:
            limiter.rollback(ip, reservation.reservation_id)
            raise

        return {"state": "queued", "remaining": reservation.remaining}

    @application.get("/api/jobs/{job_id}/status")
    def status(job_id: str, x_job_token: str | None = Header(default=None)):
        try:
            return manager.snapshot(job_id, token_value(x_job_token))
        except PermissionError:
            return error("INVALID_JOB_TOKEN", "工作驗證失敗，請重新上傳影片。", 403)
        except KeyError:
            return error("JOB_EXPIRED", "工作已過期，請重新上傳影片。", 404)

    @application.get("/api/jobs/{job_id}/download")
    def download(job_id: str, x_job_token: str | None = Header(default=None)):
        try:
            record = manager.require(job_id, token_value(x_job_token))
        except PermissionError:
            return error("INVALID_JOB_TOKEN", "工作驗證失敗，請重新上傳影片。", 403)
        except KeyError:
            return error("JOB_EXPIRED", "工作已過期，請重新上傳影片。", 404)
        if record.state is not JobState.COMPLETED or record.output is None:
            return error("JOB_NOT_READY", "影片尚未處理完成。", 409)
        return FileResponse(
            record.output,
            media_type="video/mp4",
            filename="watermark-removed.mp4",
        )

    @application.delete("/api/jobs/{job_id}", status_code=204)
    def delete(job_id: str, x_job_token: str | None = Header(default=None)):
        try:
            manager.delete(job_id, token_value(x_job_token))
        except PermissionError:
            return error("INVALID_JOB_TOKEN", "工作驗證失敗，請重新上傳影片。", 403)
        except KeyError:
            return error("JOB_EXPIRED", "工作已過期，請重新上傳影片。", 404)
        except RuntimeError as exception:
            return error("JOB_BUSY", str(exception), 409)

    web_root = Path(__file__).parent / "web"
    if web_root.exists():
        application.mount("/", StaticFiles(directory=web_root, html=True), name="web")
    return application


app = create_app()
