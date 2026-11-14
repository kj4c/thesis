# gui agent

A browser agent for blind and low-vision users: you give it a task in plain
English, it works in a real browser step by step, and it asks before it acts.
The agent is a LangGraph pipeline (`graph.py`, `nodes.py`) that drives
[browser-use](https://github.com/browser-use/browser-use). It runs from a
desktop app built for VoiceOver, a terminal chat, or a small http server that
takes prompts from a pair of Meta smart glasses.

The agent as frozen for the report's comparison is tagged **`evaluated-agent`**
(`90bbe10`); every evaluation run records its own commit in its `config.json`.

## setup

Requires Python 3.14.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install browser-use langgraph wxPython aiohttp zeroconf sounddevice numpy google-genai python-dotenv google-api-python-client google-auth-httplib2 google-auth-oauthlib keyring cryptography
playwright install chromium   # browser-use drives a real Chromium instance
cp .env.example .env          # then fill in your api keys
```

The models are chosen in `.env` (defaults in `llm.py`); fill in the key for
each provider you use:

- `PLANNER_PROVIDER`: the model that plans each step and checks steps from
  the element list. every evaluation run used `anthropic` (`claude-sonnet-5-5`).
- `VISION_PROVIDER`: checks clicks from a screenshot, is the only checker in
  vision only, and lets the front desk (task or question?) look at the page.
  `USE_VISION=false` turns it off in the other versions, and the planner
  model then answers the front desk alone.
- `EXTRACTION_PROVIDER`: reads a whole page for the `extract` action.
- `GLASSES_VISION_PROVIDER`: describes photos from the glasses.
- `GOOGLE_API_KEY`: also needed for voice transcription in the desktop app.

## running it

| how | command | asks before a step |
|---|---|---|
| desktop app | `python run.py` | per the app's settings (see [`gui/README.md`](gui/README.md)) |
| terminal chat | `python mvp.py` | every step, at the prompt: `y`, `n` (optionally with a reason), or `stop` |
| glasses server | `python serve.py` (`GLASSES_PORT` to change the port 8765) | never: the phone can't answer yet |
| evaluation | `python evaluation/run_eval.py --label <name>` | never (see [`evaluation/README.md`](evaluation/README.md)) |

`serve.py` and the desktop app's glasses listener use the same port and the
same `/data` and `/health` endpoints, so run one or the other. To reach the
Mac from the phone on any network, see "connecting the glasses" in
[`gui/README.md`](gui/README.md).

## versions

Four settings in `.env` (or on the command line) pick the agent's version:

| setting | values | what it does |
|---|---|---|
| `PERCEPTION` | `dom` (default), `hybrid`, `vision` | what the planner sees: the element list, the list plus a screenshot with numbered boxes, or the screenshot only |
| `VERIFIER` | `on` (default), `off` | whether a model checks clicks and answers before they happen |
| `VISION_CLEAN` | `off` (default), `on` | vision only: also send a screenshot without the numbers |
| `PRICE_TUNING` | `true` (default), `false` | the glasses demo's price-lookup rules and shortcuts; evaluation runs set it `false` |

The comparison's four versions are `dom`/`on`, `hybrid`/`on`, `vision`/`on`
and `vision`/`off`.

## tests

```bash
pip install pytest pytest-asyncio
pytest                      # offline: fake models + fake browser, a few seconds
RUN_LIVE=1 pytest -m live   # real browser; the last test also uses your api keys
```
