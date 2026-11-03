# read the POV photo from the glasses and turn it into text the browser agent can use.

import base64
from pathlib import Path

from pydantic import BaseModel, Field
from browser_use.llm.messages import (
    SystemMessage,
    UserMessage,
    ContentPartTextParam,
    ContentPartImageParam,
    ImageURL,
)

from llm import make_glasses_vision_llm, _ainvoke_with_retry

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class GlassesImageDescription(BaseModel):
    summary: str = Field(description="What the user is looking at, in 1-2 sentences")
    product_name: str | None = Field(
        default=None,
        description="Specific product name if visible or strongly inferable",
    )
    brand: str | None = Field(default=None, description="Brand name if visible")
    search_query: str = Field(
        description="Best short search query to find this product online (e.g. on Amazon or Google)",
    )


GLASSES_VISION_SYSTEM = SystemMessage(content=(
    "The user is wearing smart glasses and took a first-person POV photo. "
    "Describe what they are looking at. If it is a product (or product packaging, "
    "a shelf label, a screen showing a product, etc.), identify it as specifically "
    "as possible and write a concrete search_query someone would type on Amazon or "
    "Google to find the exact or closest match. If you cannot identify a product, "
    "still describe the scene and suggest the best search_query you can."
))


def _image_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return "image/jpeg"


async def describe_glasses_image(image_path: Path, user_prompt: str) -> GlassesImageDescription:
    b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    mime = _image_mime(image_path)
    parts = [
        ContentPartTextParam(text=f'User voice command: "{user_prompt}"'),
        ContentPartImageParam(image_url=ImageURL(url=f"data:{mime};base64,{b64}")),
    ]
    llm = make_glasses_vision_llm()
    response = await _ainvoke_with_retry(
        llm,
        [GLASSES_VISION_SYSTEM, UserMessage(content=parts)],
        GlassesImageDescription,
    )
    return response.completion


def format_enriched_prompt(user_prompt: str, desc: GlassesImageDescription) -> str:
    lines = [
        f'User said: "{user_prompt}"',
        "",
        "Glasses POV photo — what the user is looking at:",
        f"- Summary: {desc.summary}",
    ]
    if desc.product_name:
        lines.append(f"- Product: {desc.product_name}")
    if desc.brand:
        lines.append(f"- Brand: {desc.brand}")
    lines.extend([
        f"- Suggested search: {desc.search_query}",
        "",
        "Use the photo context above to carry out the user's request. "
        "If they said 'this product' or similar, they mean the item in the photo.",
    ])
    return "\n".join(lines)


async def enrich_prompt_with_image(image_path: Path, user_prompt: str) -> str:
    if image_path.suffix.lower() not in _IMAGE_SUFFIXES:
        return user_prompt
    desc = await describe_glasses_image(image_path, user_prompt)
    return format_enriched_prompt(user_prompt, desc)
