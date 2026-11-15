"""Settings the blog pipeline runs with (from app settings, or from the bare environment)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class BlogPipelineConfig:
    gemini_api_key: str
    gemini_model: str
    supabase_url: str
    supabase_service_key: str
    owner_name: str = "Yabibal"
    portfolio_url: str = "https://yabibal.site"
    default_og_image_url: str | None = None
    # Native image models: request IMAGE modality or the API often returns text-only (no cover bytes).
    blog_image_model: str = "gemini-2.5-flash-image"
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: str | None = None
    cloudinary_folder: str = "aegis/blog"
    blog_generate_images: bool = True
    pipeline_run_key: str | None = None
    # When set (draft | review | published) it wins over BLOG_POST_STATUS_AFTER_WRITE.
    post_status_override: str | None = None

    @classmethod
    def from_full_settings(cls, settings: Any) -> BlogPipelineConfig:
        return cls(
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.gemini_model,
            supabase_url=settings.supabase_url,
            supabase_service_key=settings.supabase_service_key,
            owner_name=settings.owner_name,
            portfolio_url=settings.portfolio_url,
            default_og_image_url=os.getenv("BLOG_DEFAULT_OG_IMAGE") or None,
            blog_image_model=os.getenv("BLOG_IMAGE_MODEL", "gemini-2.5-flash-image").strip(),
            cloudinary_cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME") or None,
            cloudinary_api_key=os.getenv("CLOUDINARY_API_KEY") or None,
            cloudinary_api_secret=os.getenv("CLOUDINARY_API_SECRET") or None,
            cloudinary_folder=os.getenv("CLOUDINARY_FOLDER", "aegis/blog").strip() or "aegis/blog",
            blog_generate_images=os.getenv("BLOG_GENERATE_IMAGES", "true").strip().lower() not in ("0", "false", "no", "off"),
            pipeline_run_key=(os.getenv("BLOG_PIPELINE_RUN_KEY") or "").strip() or None,
        )

    @classmethod
    def from_minimal_env(cls) -> BlogPipelineConfig:
        """Standalone scripts: only required keys in the environment."""
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        url = os.environ.get("SUPABASE_URL", "").strip()
        svc = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not key or not url or not svc:
            raise ValueError(
                "Set GEMINI_API_KEY, SUPABASE_URL, and SUPABASE_SERVICE_KEY in the environment."
            )
        model_raw = os.environ.get("GEMINI_MODEL", "").strip()
        return cls(
            gemini_api_key=key,
            gemini_model=model_raw or "gemini-2.5-flash",
            supabase_url=url,
            supabase_service_key=svc,
            owner_name=os.environ.get("OWNER_NAME", "Yabibal").strip(),
            portfolio_url=os.environ.get("PORTFOLIO_URL", "https://yabibal.site").strip(),
            default_og_image_url=os.getenv("BLOG_DEFAULT_OG_IMAGE") or None,
            blog_image_model=os.getenv("BLOG_IMAGE_MODEL", "gemini-2.5-flash-image").strip(),
            cloudinary_cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME") or None,
            cloudinary_api_key=os.getenv("CLOUDINARY_API_KEY") or None,
            cloudinary_api_secret=os.getenv("CLOUDINARY_API_SECRET") or None,
            cloudinary_folder=os.getenv("CLOUDINARY_FOLDER", "aegis/blog").strip() or "aegis/blog",
            blog_generate_images=os.getenv("BLOG_GENERATE_IMAGES", "true").strip().lower() not in ("0", "false", "no", "off"),
            pipeline_run_key=(os.getenv("BLOG_PIPELINE_RUN_KEY") or "").strip() or None,
        )
