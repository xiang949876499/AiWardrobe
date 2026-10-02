from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.ai import AiAnalysisError
from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import Garment, PurchaseCandidate, UploadSession
from app.routers import assets, auth, decision, garments, outfits, pagination, preferences, purchase, reports, shopping, tasks, uploads, weather, wedding
from app.task_service import TaskServiceError


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    if settings.environment == "production" and not settings.testing:
        with get_session_factory()() as db:
            legacy_count = sum((
                db.scalar(select(func.count()).select_from(Garment).where(Garment.asset_id.is_(None), Garment.image_key != "")) or 0,
                db.scalar(select(func.count()).select_from(PurchaseCandidate).where(PurchaseCandidate.asset_id.is_(None), PurchaseCandidate.image_key != "")) or 0,
                db.scalar(select(func.count()).select_from(UploadSession).where(UploadSession.asset_id.is_(None), UploadSession.original_image_key != "")) or 0,
            ))
        if legacy_count:
            raise RuntimeError(
                f"{legacy_count} legacy images still use public storage; run `python -m app.asset_migration --apply` before starting production."
            )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            settings.frontend_origin,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(garments.router)
    app.include_router(uploads.router)
    app.include_router(outfits.router)
    app.include_router(preferences.router)
    app.include_router(purchase.router)
    app.include_router(reports.router)
    app.include_router(shopping.router)
    app.include_router(weather.router)
    app.include_router(tasks.router)
    app.include_router(assets.router)
    app.include_router(decision.router)
    app.include_router(pagination.router)

    @app.exception_handler(AiAnalysisError)
    async def handle_ai_analysis_error(_request: Request, exc: AiAnalysisError) -> JSONResponse:
        status_code = 503 if exc.code == "provider_credentials_missing" else 502
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": {
                    "code": exc.code,
                    "retryable": exc.retryable,
                    "message": "暂时无法完成识别，请稍后重试或手动填写衣物信息。",
                }
            },
        )

    @app.exception_handler(TaskServiceError)
    async def handle_task_service_error(_request: Request, exc: TaskServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "retryable": exc.retryable}},
        )

    app.include_router(wedding.router)
    from app.wedding_authoring import router as authoring_router
    app.include_router(authoring_router)
    from app.wedding_creation import router as creation_router
    app.include_router(creation_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    from app.wedding_invitations import router as invitation_router
    app.include_router(invitation_router)
    from app.wedding_revisions import router as revision_router
    app.include_router(revision_router)
    return app


app = create_app()
