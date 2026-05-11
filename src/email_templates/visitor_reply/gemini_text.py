"""Extract plain text from Gemini ``GenerateContentResponse`` (blocked / empty safe)."""


def gemini_response_text(resp: object) -> str:
    try:
        t = getattr(resp, "text", None)
        if isinstance(t, str) and t.strip():
            return t.strip()
    except Exception:
        pass
    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if isinstance(t, str) and t.strip():
                return t.strip()
    return ""
