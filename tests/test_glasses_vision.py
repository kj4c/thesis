# the photo from the glasses is turned into text context for the agent. the
# format matters: reply_format reads the brand back out of it.

from pathlib import Path

import glasses_vision
from glasses_vision import GlassesImageDescription, enrich_prompt_with_image, format_enriched_prompt
from reply_format import _target_brand
from conftest import FakeLLM, has_image


def _desc(**kw):
    base = dict(summary="a bottle of shampoo on a shelf", search_query="head and shoulders 400ml")
    return GlassesImageDescription(**{**base, **kw})


def test_enriched_prompt_keeps_the_users_words_and_photo_context():
    text = format_enriched_prompt("how much is this", _desc(brand="Head & Shoulders", product_name="Classic Clean"))
    assert 'User said: "how much is this"' in text
    assert "- Brand: Head & Shoulders" in text
    assert "- Product: Classic Clean" in text
    assert "- Suggested search: head and shoulders 400ml" in text


def test_missing_brand_and_product_lines_are_omitted():
    text = format_enriched_prompt("what is this", _desc())
    assert "- Brand:" not in text and "- Product:" not in text


def test_brand_round_trips_into_reply_formatting():
    text = format_enriched_prompt("how much is this", _desc(brand="Dove"))
    assert _target_brand(text) == "Dove"


async def test_non_image_media_skips_vision(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00")
    assert await enrich_prompt_with_image(clip, "what is this") == "what is this"


async def test_photo_is_sent_to_the_vision_model(monkeypatch, tmp_path):
    llm = FakeLLM(_desc(brand="Dove"))
    monkeypatch.setattr(glasses_vision, "make_glasses_vision_llm", lambda: llm)
    photo = tmp_path / "pic.JPG"
    photo.write_bytes(b"\xff\xd8\xff\xe0")

    text = await enrich_prompt_with_image(photo, "how much is this")

    assert "- Brand: Dove" in text
    user = llm.calls[0][0][1]
    assert has_image(user)
    assert user.content[1].image_url.url.startswith("data:image/jpeg;base64,")


def test_mime_types():
    assert glasses_vision._image_mime(Path("a.png")) == "image/png"
    assert glasses_vision._image_mime(Path("a.webp")) == "image/webp"
    assert glasses_vision._image_mime(Path("a.JPEG")) == "image/jpeg"
