# based on demo_part3 by KJ
# entry point: run the agent as an interactive chat. the pieces live in
# llm.py (model), state.py (state + resources), nodes.py (the nodes),
# graph.py (the graph). run with:  python mvp.py

import asyncio
import time
from langchain_core.runnables import RunnableConfig

import llm  # for PLANNER_PROVIDER / PLANNER_MODELS (to report the model in use)
from graph import build_agent, build_workflow
from state import _start_resources, _run_config


async def run_pipeline(query: str):
    # TODO: connect to main properly once it is finished
    workflow = build_workflow()
    config: RunnableConfig = {"configurable": {"thread_id": "1"}}
    # note: the async entrypoint is `ainvoke`, not `invoke`
    await workflow.ainvoke({"query": query}, config)


async def run_turn(workflow, query: str, config: RunnableConfig):
    # run one user turn. the user message gets appended to the saved transcript
    # (operator.add reducer), so history piles up across turns.
    await workflow.ainvoke(
        {"query": query, "messages": [{"role": "user", "content": query}]},
        config,
    )


def last_assistant_reply(workflow, config: RunnableConfig) -> str:
    msgs = workflow.get_state(config).values.get("messages", [])
    for m in reversed(msgs):
        if m.get("role") == "assistant":
            return str(m.get("content", ""))
    return "Task completed but no response was generated."


async def run_turn_with_result(workflow, query: str, config: RunnableConfig) -> str:
    await run_turn(workflow, query, config)
    from reply_format import compact_glasses_reply
    return compact_glasses_reply(query, last_assistant_reply(workflow, config))


async def run_once(query: str, thread_id: str = "1"):
    # single-shot: one prompt, then tear down. handy for non-interactive tests.
    session, tools, file_system = await _start_resources()
    try:
        workflow = build_agent()
        await run_turn(workflow, query, _run_config(session, tools, file_system, thread_id))
    finally:
        await session.kill()


async def run_chat(thread_id: str = "1"):
    # interactive chat: one browser + one thread_id shared across turns, so the
    # agent remembers the conversation. type 'quit' (or ctrl+c / ctrl+d) to exit.
    session, tools, file_system = await _start_resources()
    workflow = build_agent()
    config = _run_config(session, tools, file_system, thread_id)
    print(f"planner: {llm.PLANNER_PROVIDER} / {llm.PLANNER_MODELS.get(llm.PLANNER_PROVIDER, '?')}")
    if llm.USE_VISION:
        print(f"vision : {llm.VISION_PROVIDER} / {llm.VISION_MODEL}  (analyse consensus gate ON)")
    else:
        print("vision : off  (analyse is DOM-only)")
    try:
        while True:
            try:
                query = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye")
                break
            if query.lower() in ("quit", "exit", ""):
                break
            # type `history` to dump what the agent currently remembers — makes
            # the chat memory easy to eyeball while testing.
            if query.lower() == "history":
                msgs = workflow.get_state(config).values.get("messages", [])
                print("── remembered so far ────────────────────")
                for m in msgs:
                    print(f"  {m['role']}: {m['content']}")
                continue
            start = time.perf_counter()
            try:
                await run_turn(workflow, query, config)
            except KeyboardInterrupt:
                # ctrl+c mid-task: stop this task but stay in the chat
                print("\n[stopped] task interrupted — still here, ask me something else.")
            print(f"⏱  {time.perf_counter() - start:.1f}s")
    finally:
        # always close the browser cleanly, however we leave the loop
        print("closing browser…")
        await session.kill()

if __name__ == "__main__":
    # interactive chat that remembers across prompts within the session
    try:
        asyncio.run(run_chat())
    except KeyboardInterrupt:
        pass
