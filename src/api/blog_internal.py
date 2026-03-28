"""Secret-protected internal trigger for blog draft pipeline."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from config.settings import Settings, get_settings
from src.blog.pipeline import BlogPipelineConfig, run_blog_draft_once

logger = logging.getLogger("aegis.blog_internal")

router = APIRouter(prefix="/internal/blog", tags=["internal"])


def verify_internal_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_internal_secret: Annotated[str | None, Header()] = None,
) -> None:
    expected = (settings.api_secret_key or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API secret not configured",
        )
    if x_internal_secret is not None and x_internal_secret.strip() == expected:
        return
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip() == expected:
            return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
    )


@router.post(
    "/generate-draft",
    dependencies=[Depends(verify_internal_secret)],
    summary="Run blog draft pipeline once (Gemini + Supabase)",
)
def generate_blog_draft(settings: Annotated[Settings, Depends(get_settings)]):
    cfg = BlogPipelineConfig.from_full_settings(settings)
    try:
        result = run_blog_draft_once(cfg)
    except Exception as e:
        logger.exception("internal blog generate failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
    return result
