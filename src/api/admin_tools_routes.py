"""Admin tools: signed image uploads (Cloudinary) and the database schema for the Data page. Secret-protected."""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from config.settings import Settings, get_settings
from src.api.admin_people_routes import TABLES
from src.api.blog_internal import verify_internal_secret

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_internal_secret)])
SettingsDep = Annotated[Settings, Depends(get_settings)]

_CLOUDINARY_URL = re.compile(r"^cloudinary://([^:]+):([^@]+)@(.+)$")
_FK = re.compile(r"<fk table='([^']+)' column='([^']+)'/>")


def cloudinary_config() -> tuple[str, str, str] | None:
    """(cloud_name, api_key, api_secret) from CLOUDINARY_URL or the three separate variables."""
    m = _CLOUDINARY_URL.match(os.environ.get("CLOUDINARY_URL", "").strip())
    if m and "<" not in m.group(2):
        return m.group(3), m.group(1), m.group(2)
    cloud, key, secret = (os.environ.get(n, "").strip() for n in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    return (cloud, key, secret) if cloud and key and secret else None


def sign(params: dict[str, str], secret: str) -> str:
    serialized = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.sha1(f"{serialized}{secret}".encode()).hexdigest()


@router.post("/uploads/sign", summary="One-time signature so the browser can upload an image straight to Cloudinary")
def sign_upload(folder: str = "site") -> dict[str, Any]:
    """
    The file never passes through this API and the API secret never reaches the browser:
    the browser posts the file to Cloudinary with this signature, which is valid for one hour
    and only for this folder.
    """
    cfg = cloudinary_config()
    if not cfg:
        raise HTTPException(
            503,
            detail="Image upload is not set up: add CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET on the API host.",
        )
    cloud, key, secret = cfg
    safe_folder = "yabibal/" + (re.sub(r"[^a-z0-9/-]", "", folder.lower()).strip("/") or "site")
    params = {"folder": safe_folder, "timestamp": str(int(time.time()))}
    return {
        "upload_url": f"https://api.cloudinary.com/v1_1/{cloud}/image/upload",
        "fields": {**params, "api_key": key, "signature": sign(params, secret)},
    }


def parse_schema(openapi: dict[str, Any], row_counts: dict[str, int | None]) -> dict[str, Any]:
    """PostgREST's OpenAPI description → tables, columns, primary keys and foreign keys."""
    tables, relations = [], []
    for name, spec in sorted((openapi.get("definitions") or {}).items()):
        if name not in TABLES:
            continue
        required = set(spec.get("required") or [])
        columns = []
        for col, info in (spec.get("properties") or {}).items():
            note = str(info.get("description") or "")
            fk = _FK.search(note)
            columns.append(
                {
                    "name": col,
                    "type": info.get("format") or info.get("type") or "",
                    "pk": "<pk/>" in note,
                    "required": col in required,
                    "fk": {"table": fk.group(1), "column": fk.group(2)} if fk else None,
                }
            )
            if fk and fk.group(1) in TABLES:
                # Many rows here point at one row there; optional when the column may be empty.
                relations.append({"from": name, "column": col, "to": fk.group(1), "to_column": fk.group(2), "optional": col not in required})
        tables.append({"table": name, "about": TABLES[name][0], "rows": row_counts.get(name), "columns": columns})
    return {"tables": tables, "relations": relations}


@router.get("/schema", summary="Tables, columns and how they reference each other")
def schema(settings: SettingsDep) -> dict[str, Any]:
    from src.admin import inbox

    try:
        r = httpx.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/",
            headers={"apikey": settings.supabase_service_key, "Authorization": f"Bearer {settings.supabase_service_key}"},
            timeout=20,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail=f"Could not read the schema from Supabase: {exc}") from exc
    client = inbox.sb(settings)
    counts: dict[str, int | None] = {}
    for name in TABLES:
        try:
            counts[name] = client.table(name).select("*", count="exact").limit(1).execute().count or 0
        except Exception:
            counts[name] = None
    return parse_schema(r.json(), counts)
