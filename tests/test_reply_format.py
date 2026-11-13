# replies are spoken through the glasses: they must be short, and price answers
# must be just the price (plus a brand warning when it's not what the user saw).

import pytest

from glasses_vision import GlassesImageDescription, format_enriched_prompt
from reply_format import compact_glasses_reply, is_price_task, price_from_text


@pytest.mark.parametrize("query", [
    "what's the price of this",
    "how much is this on amazon",
    "find the cheapest spoon",
    "What does it COST",
    "search for this on amazon",
])
def test_price_intent_detected(query):
    assert is_price_task(query)


@pytest.mark.parametrize("query", [
    "open google", "add this to my cart", "what's on this page", "", None,
])
def test_non_price_queries_not_flagged(query):
    assert not is_price_task(query)


@pytest.mark.parametrize("text,expected", [
    ("The spoon costs $12.99 today", "$12.99"),
    ("$1,299.00 for the TV", "$1,299.00"),
    ("only $5", "$5"),
    ("no price here", None),
    ("", None),
    (None, None),
])
def test_price_extraction(text, expected):
    assert price_from_text(text) == expected


def test_price_task_reply_is_only_the_price():
    reply = compact_glasses_reply(
        "how much is this spoon", "The cheapest spoon on Amazon is $4.99 from Oneida, shipped free.",
    )
    assert reply == "$4.99"


def _enriched(brand=None, product=None, search="scar gel"):
    desc = GlassesImageDescription(summary="a tube of scar gel", brand=brand,
                                   product_name=product, search_query=search)
    return format_enriched_prompt("how much is this on amazon", desc)


def test_same_brand_reply_has_no_warning():
    reply = compact_glasses_reply(_enriched(brand="ScarAway"), "Scar Away silicone gel - $19.99")
    assert reply == "$19.99"


def test_different_brand_is_flagged():
    reply = compact_glasses_reply(_enriched(brand="Mederma"), "ScarAway silicone gel - $19.99")
    assert reply == "$19.99, different brand"


def test_generic_product_words_dont_count_as_brand_match():
    # "silicone" / "scar" / "gel" are product words, not a brand
    reply = compact_glasses_reply(_enriched(brand="Silicone Scar Gel"), "Generic thing $3.00")
    assert reply == "$3.00"  # no distinctive brand word -> can't tell -> no warning


def test_long_non_price_reply_is_trimmed_for_speech():
    long = "I opened the page and " + "found lots of interesting things " * 10
    reply = compact_glasses_reply("open the news", long)
    assert len(reply) <= 80
    assert reply.endswith("…")
    assert not reply[:-1].endswith(" ")


def test_only_first_line_is_spoken():
    assert compact_glasses_reply("open google", "Opened Google.\nDetails: ...") == "Opened Google."


def test_empty_reply_stays_empty():
    assert compact_glasses_reply("anything", "") == ""
    assert compact_glasses_reply("anything", None) == ""
