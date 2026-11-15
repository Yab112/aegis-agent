"""Cover image: prompt, Gemini image generation, Cloudinary upload."""
from __future__ import annotations

import base64
import hashlib
import logging
import time
from typing import Any

import requests

from src.blog.config import BlogPipelineConfig
from src.blog.llm import _extract_json_object, genai, genai_protos
from src.blog.prompts import IMAGE_PROMPT_PROMPT

logger = logging.getLogger("aegis.blog")


def _extract_image_bytes_from_gemini_response(resp: Any) -> tuple[bytes, str] | None:
    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if not inline:
                continue
            mime_type = getattr(inline, "mime_type", None) or "image/png"
            data = getattr(inline, "data", None)
            if isinstance(data, bytes) and data:
                return data, mime_type
            if isinstance(data, str) and data.strip():
                try:
                    return base64.b64decode(data), mime_type
                except Exception:
                    continue

    # Some SDK variants expose parts directly on response.
    parts = getattr(resp, "parts", None) or []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if not inline:
            continue
        mime_type = getattr(inline, "mime_type", None) or "image/png"
        data = getattr(inline, "data", None)
        if isinstance(data, bytes) and data:
            return data, mime_type
        if isinstance(data, str) and data.strip():
            try:
                return base64.b64decode(data), mime_type
            except Exception:
                continue
    return None


def _build_cover_prompt(
    model: Any,
    gen_cfg: Any,
    *,
    title: str,
    description: str,
    angle: str,
    tags: list[str],
) -> tuple[str, str]:
    tags_hint = ", ".join(tags) if tags else "general"
    raw = model.generate_content(
        IMAGE_PROMPT_PROMPT.format(
            title=title,
            description=description,
            angle=angle or title,
            tags=tags_hint,
        ),
        generation_config=gen_cfg,
    ).text
    payload = _extract_json_object(raw or "")
    image_prompt = str(payload.get("image_prompt") or "").strip()
    image_alt = str(payload.get("image_alt") or "").strip()
    if not image_prompt:
        image_prompt = (
            f"Editorial style technical illustration about {title}. "
            "No text, no logos, no watermark. Clean lighting, modern composition."
        )
    if not image_alt:
        image_alt = f"Cover image for {title}"
    return image_prompt[:1200], image_alt[:280]


def _cover_image_generation_config() -> dict[str, Any]:
    """Gemini image endpoints expect image output modality (see Google image-generation docs)."""
    return {
        "response_modalities": [genai_protos.GenerationConfig.Modality.IMAGE],
    }


def _generate_cover_image_bytes(cfg: BlogPipelineConfig, image_prompt: str) -> tuple[bytes, str] | None:
    try:
        image_model = genai.GenerativeModel(cfg.blog_image_model)
        resp = image_model.generate_content(
            image_prompt,
            generation_config=_cover_image_generation_config(),
        )
        out = _extract_image_bytes_from_gemini_response(resp)
        if out is None:
            fr = None
            br = None
            try:
                cands = getattr(resp, "candidates", None) or []
                if cands:
                    fr = getattr(cands[0], "finish_reason", None)
                pf = getattr(resp, "prompt_feedback", None)
                if pf is not None:
                    br = getattr(pf, "block_reason", None)
            except Exception:
                pass
            logger.warning(
                "cover image: model=%s returned no inline image (finish_reason=%s prompt_block=%s). "
                "Check BLOG_IMAGE_MODEL (use a native image model) and API quotas.",
                cfg.blog_image_model,
                fr,
                br,
            )
        return out
    except Exception as e:
        logger.warning("cover image generation failed for model=%s: %s", cfg.blog_image_model, e)
        return None


def _cloudinary_signature(params: dict[str, str], api_secret: str) -> str:
    serialized = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.sha1(f"{serialized}{api_secret}".encode("utf-8")).hexdigest()


def _upload_cover_to_cloudinary(
    cfg: BlogPipelineConfig,
    *,
    slug: str,
    image_bytes: bytes,
    mime_type: str,
    image_prompt: str,
) -> str | None:
    cloud = (cfg.cloudinary_cloud_name or "").strip()
    key = (cfg.cloudinary_api_key or "").strip()
    secret = (cfg.cloudinary_api_secret or "").strip()
    if not cloud or not key or not secret:
        logger.info("cloudinary not configured, skip generated cover upload")
        return None

    ts = str(int(time.time()))
    # Every signed field except api_key, file, signature must be included in the string to sign
    # (sending overwrite/tags without signing caused invalid-signature uploads → no secure_url).
    sign_params = {
        "folder": cfg.cloudinary_folder,
        "public_id": f"{slug}-cover",
        "timestamp": ts,
        "overwrite": "true",
        "tags": "aegis,blog,cover",
    }
    signature = _cloudinary_signature(sign_params, secret)

    url = f"https://api.cloudinary.com/v1_1/{cloud}/image/upload"
    form = {
        "api_key": key,
        "signature": signature,
        **sign_params,
    }
    ext = "png"
    if "/" in mime_type:
        ext = mime_type.split("/", 1)[1].lower()
        if ext == "jpeg":
            ext = "jpg"
    files = {"file": (f"{slug}-cover.{ext}", image_bytes, mime_type)}
    try:
        resp = requests.post(url, data=form, files=files, timeout=60)
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        secure_url = str(payload.get("secure_url") or "").strip()
        return secure_url or None
    except requests.HTTPError as e:
        detail = ""
        if e.response is not None:
            try:
                detail = (e.response.text or "")[:400]
            except Exception:
                pass
        logger.warning("cloudinary upload failed for slug=%s: %s %s", slug, e, detail)
        return None
    except Exception as e:
        logger.warning("cloudinary upload failed for slug=%s: %s", slug, e)
        return None
