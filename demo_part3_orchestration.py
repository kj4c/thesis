import asyncio
import os
import re
from typing import Any, TypedDict

from browser_use import Agent, ChatGoogle, ChatOpenAI
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph

load_dotenv()

class PipelineState(TypedDict, total=False):
    # Natural language shopping request, e.g.:
    # "Find a wireless mouse under $50 for everyday office use"
    query: str
    # Plain-text list of candidate products from the first agent
    raw_results: str
    # LLM analysis + human decision metadata
    analysis: str
    # Whether we need the human to answer a question before proceeding
    needs_human_answer: bool
    # The question to show the human (e.g. “Approve buying X for $Y?”)
    question: str
    # Human decision captured by the caller (True = proceed, False = stop)
    human_approved: bool
    # Result of the “add to cart” agent
    final: str

# ── Node 1: browser-use product search agent ─────────────────────────────────

async def search_agent_node(query: str) -> str:
    task = (
        "You are a shopping assistant.\n"
        f"1. Go to https://www.amazon.com.\n"
        f"2. Search for products that satisfy this request: '{query}'.\n"
        "3. From the results, pick the 3 best candidates that are actually buyable items "
        "(not ads or bundles).\n"
        "4. For each candidate, return exactly one line in this format:\n"
        "   'N. [title] - [price] - [url]'\n"
        "   where N is 1, 2, or 3.\n"
        "Return only those three lines as plain text, nothing else.\n\n"
        "IMPORTANT:\n"
        "- Do NOT write any files (no write_file / read_file).\n"
        "- Do NOT type the answer into any web page fields.\n"
        "- As soon as you have the three lines, immediately finish using the done action "
        "with exactly those three lines."
    )

    # Gemini free-tier quotas are easy to exhaust; keep it as a first attempt,
    # but fall back quickly to OpenAI so the workflow still completes.
    try:
        history = await Agent(
            task=task,
            llm=ChatGoogle(model="gemini-2.5-flash"),
            use_vision=False,
            enable_planning=False,
            use_judge=False,
            loop_detection_enabled=False,
            max_failures=2,
            display_files_in_done_text=False,
            available_file_paths=[],
        ).run()
        result = (history.final_result() or "").strip()
    except Exception:
        result = ""

    if not result or result.lower() == "no result found.":
        history = await Agent(
            task=task,
            llm=ChatOpenAI(model="gpt-4o-mini"),
            use_vision=False,
            enable_planning=False,
            use_judge=False,
            loop_detection_enabled=False,
            max_failures=2,
            display_files_in_done_text=False,
            available_file_paths=[],
        ).run()
        result = (history.final_result() or "").strip()

    result = result or "No result found."
    print(f"\n[Agent 1 output]\n{result}\n")
    return result


# ── Node 2: LangChain analysis + human-in-the-loop check ─────────────────────

async def analysis_chain_node(raw_results: str, original_query: str) -> str:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a shopping advisor helping a human buy a product online.\n"
                "You will receive:\n"
                "- The user's shopping request.\n"
                "- Three candidate products (title, price, URL) scraped from Amazon.\n\n"
                "Your job:\n"
                "1. Briefly compare the options (price, likely quality, obvious red flags).\n"
                "2. Recommend ONE product to put in the cart.\n"
                "3. Decide whether a human confirmation is needed before buying "
                "(e.g. unknown brand, many red flags, very expensive).\n"
                "4. Decide whether we should do an extra brand-reputation check "
                "using another browser agent before buying.\n\n"
                "Output MUST be valid JSON with this shape:\n"
                "{\n"
                "  \"summary\": \"3 short bullet points summarizing tradeoffs\",\n"
                "  \"recommended_index\": 1,\n"
                "  \"recommended_url\": \"https://...\",\n"
                "  \"recommended_brand\": \"BrandName or Unknown\",\n"
                "  \"needs_human_confirmation\": true,\n"
                "  \"run_brand_check_agent\": true,\n"
                "  \"reason\": \"one short sentence\"\n"
                "}\n"
                "Do not add any extra keys or text outside this JSON object.",
            ),
            ("human", "User request: {query}\n\nCandidates:\n{results}"),
        ]
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0,
        retries=0,
        request_timeout=8,
    )
    chain = prompt | llm | StrOutputParser()

    try:
        analysis = (await chain.ainvoke({"query": original_query, "results": raw_results}) or "").strip()
    except Exception:
        analysis = ""

    if not analysis:
        analysis = heuristic_analysis_fallback(raw_results, original_query)
    print(f"\n[Chain output]\n{analysis}\n")
    return analysis


def heuristic_analysis_fallback(raw_results: str, original_query: str) -> str:
    lines = [ln.strip() for ln in raw_results.splitlines() if ln.strip()]
    picked: list[tuple[str, str]] = []
    for ln in lines:
        m = re.match(r"^\s*(\d+)\.\s*(.+?)\s*-\s*(https?://\S+)\s*$", ln)
        if not m:
            continue
        title = m.group(2).strip()
        url = m.group(3).strip().rstrip(").,")
        picked.append((title, url))
        if len(picked) >= 3:
            break

    if not picked:
        return (
            "- (Result 1) No parseable search results were returned.\n"
            "- (Result 2) Try re-running with a different query.\n"
            "- (Result 3) If Google blocks automation, switch to a different source site."
        )

    bullets = []
    for i, (title, url) in enumerate(picked, start=1):
        bullets.append(f"- (Result {i}) {title} — {url}")
    return "\n".join(bullets)


# ── Node 3: second browser-use agent adds to cart (guided by analysis) ───────

async def action_agent_node(analysis: str, original_query: str) -> str:
    task = (
        "You are a shopping automation agent.\n\n"
        f"User request: '{original_query}'.\n\n"
        "You also receive a JSON blob from a shopping advisor LLM that looks like this:\n"
        "{\n"
        '  \"summary\": \"...\",\n'
        '  \"recommended_index\": N,\n'
        '  \"recommended_url\": \"https://...\",\n'
        '  \"recommended_brand\": \"...\",\n'
        '  \"needs_human_confirmation\": true/false,\n'
        '  \"run_brand_check_agent\": true/false,\n'
        '  \"reason\": \"...\"\n'
        "}\n\n"
        f"JSON from advisor:\n{analysis}\n\n"
        "Your job:\n"
        "1. If the JSON says needs_human_confirmation=true, assume the human has ALREADY said yes.\n"
        "2. Ignore run_brand_check_agent (do not do any extra brand or scam checks).\n"
        "3. Navigate directly to the recommended_url product page.\n"
        "4. If at any point you are on a login or sign-in page (email + password fields, or a 'Continue' "
        "button that clearly belongs to a login flow), you must IMMEDIATELY stop all further actions. "
        "Do NOT type into any login fields and do NOT click any login or continue buttons. Instead, "
        "finish by explaining that checkout cannot proceed because login is required.\n"
        "5. If the site does NOT require login and everything looks normal on the product page or cart, "
        "add exactly ONE unit of the product to the cart.\n"
        "6. Finally, return a short plain-text confirmation including: product title, price if visible, "
        "and whether it was successfully added to the cart."
    )

    history = await Agent(
        task=task,
        llm=ChatGoogle(model="gemini-flash-latest"),
        use_vision=False,
        enable_planning=False,
        use_judge=False,
        loop_detection_enabled=False,
        max_failures=2,
    ).run()
    result = (history.final_result() or "").strip()

    if not result or result.lower() == "could not retrieve page.":
        history = await Agent(
            task=task,
            llm=ChatOpenAI(model="gpt-4o-mini"),
            use_vision=False,
            enable_planning=False,
            use_judge=False,
            loop_detection_enabled=False,
            max_failures=2,
        ).run()
        result = (history.final_result() or "").strip()

    result = result or "Could not retrieve page."
    print(f"\n[Agent 3 output]\n{result}\n")
    return result


# ── LangGraph workflow ───────────────────────────────────────────────────────

async def node_search(state: PipelineState) -> PipelineState:
    raw_results = await search_agent_node(state["query"])
    return {"raw_results": raw_results}


async def node_analyze(state: PipelineState) -> PipelineState:
    analysis = await analysis_chain_node(state.get("raw_results", ""), state["query"])

    needs_human = False
    question = ""

    # Heuristic: if advisor explicitly says human confirmation is needed in its JSON,
    # or if we fell back to the heuristic summary, flag a question for the user.
    if '"needs_human_confirmation": true' in analysis.lower().replace(" ", ""):
        needs_human = True
        question = (
            "The advisor recommends a product but marked needs_human_confirmation=true.\n"
            f"Advisor output:\n{analysis}\n\n"
            "Do you want to proceed with adding this recommended product to the cart? (yes/no)"
        )
    elif "No parseable search results were returned." in analysis:
        needs_human = True
        question = (
            "The AI could not confidently parse any Amazon results for your request.\n"
            f"Advisor output:\n{analysis}\n\n"
            "Do you still want to proceed and let the agent try to add something to the cart? (yes/no)"
        )

    return {
        "analysis": analysis,
        "needs_human_answer": needs_human,
        "question": question,
    }


async def node_act(state: PipelineState) -> PipelineState:
    # If a previous node requested human input and we don't yet have approval,
    # surface the question and do NOT perform browser actions.
    if state.get("needs_human_answer") and not state.get("human_approved"):
        return {
            "final": (
                "Awaiting human decision before adding to cart.\n\n"
                f"Question:\n{state.get('question', 'No question set.')}"
            ),
            "needs_human_answer": True,
            "question": state.get("question", ""),
        }

    final = await action_agent_node(state.get("analysis", ""), state["query"])
    return {"final": final}


def build_workflow():
    g: StateGraph[PipelineState] = StateGraph(PipelineState)
    g.add_node("search", node_search)
    g.add_node("analyze", node_analyze)
    g.add_node("act", node_act)

    g.add_edge(START, "search")
    g.add_edge("search", "analyze")
    g.add_edge("analyze", "act")
    g.add_edge("act", END)
    return g.compile()


async def run_pipeline(query: str) -> str:
    print(f"=== Pipeline starting for: '{query}' ===\n")
    app = build_workflow()
    out: dict[str, Any] = await app.ainvoke({"query": query})
    print("=== Pipeline complete ===")
    return str(out.get("final", ""))


if __name__ == "__main__":
    asyncio.run(run_pipeline("spoons"))