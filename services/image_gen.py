import base64
import os

import requests

MINIMAX_ENDPOINT = "https://api.minimax.io/v1/image_generation"
MINIMAX_MODEL = "image-01"

BRAND_PROMPT = (
    "{subject}, bioluminescent moonlight aesthetic, "
    "deep purple and teal tones, subtle Filipino mystical symbolism, "
    "editorial illustration style, soft glow, ritual energy, "
    "no text, high detail"
)


def build_prompt(subject: str) -> str:
    return BRAND_PROMPT.format(subject=subject.strip())


def generate_image(
    subject: str,
    aspect_ratio: str = "16:9",
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
    timeout: int = 60,
) -> bytes:
    key = api_key or os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY is not set")
    http = session or requests
    r = http.post(
        MINIMAX_ENDPOINT,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": MINIMAX_MODEL,
            "prompt": build_prompt(subject),
            "aspect_ratio": aspect_ratio,
            "response_format": "base64",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    images = (payload.get("data") or {}).get("image_base64") or []
    if not images:
        raise RuntimeError(f"MiniMax response missing data.image_base64: {payload}")
    return base64.b64decode(images[0])
