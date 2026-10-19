# the swift app and the python servers are written separately; these tests read
# the swift source so a renamed key on either side fails here instead of on the
# phone.

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWIFT = (ROOT / "testApp.swift").read_text()
INTENT = (ROOT / "ios" / "GlassesAgentIntent.swift").read_text()


def _struct_fields(name: str) -> set[str]:
    body = re.search(rf"struct {name}\b[^{{]*\{{(.*?)\n\}}", SWIFT, re.S).group(1)
    return set(re.findall(r"let (\w+):", body))


def test_response_struct_matches_server_reply_keys():
    # serve.py / glasses_server.py reply with {status, id, result} or {status, id, error}
    assert _struct_fields("BrainResponse") >= {"status", "id", "result", "error"}


def test_response_fields_are_optional():
    # 400s only carry {"error": ...}; non-optional fields would fail to decode
    body = re.search(r"struct BrainResponse\b.*?\n\}", SWIFT, re.S).group(0)
    assert re.findall(r"let \w+: \w+(?!\?)\s*$", body, re.M) == []


def test_app_posts_the_payload_keys_the_server_reads():
    sent = set(re.findall(r'payload\["(\w+)"\]', SWIFT)) | set(re.findall(r'"(prompt)":', SWIFT))
    assert sent >= {"prompt", "media_data", "media_type", "filename"}


def test_app_targets_the_data_endpoint_on_the_default_port():
    assert '/data"' in SWIFT
    assert 'var port = "8765"' in SWIFT


def test_app_waits_long_enough_for_an_agent_run():
    timeout = float(re.search(r"timeoutInterval = (\d+)", SWIFT).group(1))
    assert timeout >= 120, "multi-step browser tasks regularly take over a minute"


def test_siri_intent_hands_its_prompt_to_the_app():
    key = re.search(r'static let prompt = "(\w+)"', SWIFT).group(1)
    assert "GlassesAgentKeys.prompt" in INTENT
    assert key == "prompt"
