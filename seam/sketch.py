"""Reading a garment sketch.

If an Anthropic key is set, a vision model reads the sketch and says what kind
of garment it is and which details it can see. It only reads; it never sets a
measurement or a price. Those come from techpack.py.

Without a key (or for SVG files, which vision models don't take), the
designer's own choices on the upload form are used as-is.
"""
import base64
import json
import logging

from . import config
from .techpack import GARMENTS

log = logging.getLogger("seam.sketch")

VISION_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

_SCHEMA = {
    "type": "object",
    "properties": {
        "garment": {"type": "string", "enum": list(GARMENTS)},
        "features": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["garment", "features", "confidence", "notes"],
    "additionalProperties": False,
}


def _prompt() -> str:
    lines = ["This is a flat technical sketch of one garment from a fashion design team.",
             "Say which garment type it is and which of the listed details you can see.",
             "Only list a detail if it is clearly drawn. Give confidence from 0 to 1.",
             "Garment types and their possible details:"]
    for code, g in GARMENTS.items():
        feats = ", ".join(f"{k} ({v['label']})" for k, v in g["features"].items())
        lines.append(f"- {code} ({g['label']}): {feats}")
    lines.append("In notes, write one short sentence on anything a pattern maker should check.")
    return "\n".join(lines)


def read(content_type: str, data: bytes, hints: dict) -> dict:
    """Returns {garment, features, confidence, notes, provider}."""
    fallback = {
        "garment": hints.get("garment"),
        "features": list(hints.get("features") or []),
        "confidence": 1.0,
        "notes": "Taken from the designer's form.",
        "provider": "designer form",
    }
    if not config.ANTHROPIC_API_KEY or content_type not in VISION_TYPES:
        return fallback

    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0)
    try:
        response = client.beta.messages.create(
            model=config.SKETCH_MODEL,
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": content_type,
                                                 "data": base64.standard_b64encode(data).decode()}},
                    {"type": "text", "text": _prompt()},
                ],
            }],
        )
    except anthropic.APIStatusError as e:
        # 4xx won't get better on retry; fall back to the form. 5xx/429 bubble
        # up so the job queue retries later.
        if e.status_code == 429 or e.status_code >= 500:
            raise
        log.warning("sketch read rejected (%s), using form", e.status_code)
        return fallback

    if response.stop_reason == "refusal":
        return fallback
    text = next((b.text for b in response.content if b.type == "text"), "")
    out = json.loads(text)
    g = GARMENTS[out["garment"]]
    return {
        "garment": out["garment"],
        "features": [f for f in out["features"] if f in g["features"]],
        "confidence": max(0.0, min(1.0, float(out["confidence"]))),
        "notes": out["notes"],
        "provider": response.model,
    }
