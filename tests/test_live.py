# opt-in checks against a real browser (and real models for the last one).
#   RUN_LIVE=1 pytest -m live

import asyncio
import json
import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to run live tests"),
]

PAGE = "https://en.wikipedia.org/wiki/Spoon"


@pytest.fixture
async def browser():
    from browser_use import BrowserSession
    s = BrowserSession(headless=True, keep_alive=True)
    await s.start()
    yield s
    await s.kill()


async def _scroll(session, y):
    cdp = await session.get_or_create_cdp_session()
    await cdp.cdp_client.send.Runtime.evaluate(
        params={"expression": f"window.scrollTo(0,{y})"}, session_id=cdp.session_id)
    await asyncio.sleep(1)
    return cdp


async def test_element_boxes_line_up_with_the_page_after_scrolling(browser):
    # the vision model clicks by box number, so boxes must sit on the real
    # element even when the page is scrolled
    await browser.navigate_to(PAGE)
    await asyncio.sleep(2)
    cdp = await _scroll(browser, 1200)
    summary = await browser.get_browser_state_summary(include_screenshot=True)

    checked = 0
    for node in summary.dom_state.selector_map.values():
        href = (node.attributes or {}).get("href", "")
        if node.tag_name.lower() != "a" or not href or href.startswith("#") or not node.absolute_position:
            continue
        r = await cdp.cdp_client.send.Runtime.evaluate(params={
            "expression": f"JSON.stringify((()=>{{const e=[...document.querySelectorAll('a')]"
                          f".find(a=>a.getAttribute('href')=={json.dumps(href)});"
                          f"if(!e)return null;const b=e.getBoundingClientRect();return [b.x,b.y]}})())",
            "returnByValue": True}, session_id=cdp.session_id)
        rect = json.loads(r["result"]["value"] or "null")
        if not rect:
            continue
        assert abs(node.absolute_position.x - rect[0]) < 2
        assert abs(node.absolute_position.y - rect[1]) < 2
        checked += 1
        if checked >= 10:
            break
    assert checked >= 5


async def test_marked_screenshot_on_a_real_page(browser):
    import nodes
    await browser.navigate_to(PAGE)
    await asyncio.sleep(2)
    summary = await browser.get_browser_state_summary(include_screenshot=True)
    marked = await nodes._marked_screenshot(browser, summary)
    assert marked and marked != summary.screenshot


async def test_real_models_answer_a_price_question(browser):
    import tempfile
    from browser_use import Tools
    from browser_use.filesystem.file_system import FileSystem
    from graph import build_agent
    from mvp import run_turn_with_result
    from state import _run_config

    config = _run_config(browser, Tools(), FileSystem(tempfile.mkdtemp()), "live")
    reply = await asyncio.wait_for(
        run_turn_with_result(build_agent(), "how much is an Oneida stainless steel teaspoon set on amazon", config),
        timeout=180,
    )
    assert "$" in reply, reply
    assert len(reply) <= 80
