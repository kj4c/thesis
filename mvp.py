# based on demo_part3 by KJ

import asyncio
from typing import TypedDict, Literal
from dotenv import load_dotenv

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, RetryPolicy, TimeoutPolicy
from langgraph.errors import NodeError


load_dotenv()

class PipelineState(TypedDict, total=False):
    # original natural language request from user
    query: str
    '''
    TODO:
    properties that update as it progresses based on current node plan
    - relevant perception information
    - adjusted query
    - current plan (to execute)
    '''
    
# ── Node 1: Conversational Node  ─────────────────────────────────────────────
'''
    This one is for questions like 'what is on this page' and 'where is the
    search bar'? May include an option to ask user if they want to switch to
    'executive mode' if it sounds like they want a specific task done. 
    
    Potentially can be reused to judge what went wrong for a task after human
    approval is denied.
'''
async def respond(query: str) -> str:
    ... # TODO
    
def is_task(query: str) -> bool:
    # either LLM or action, depending on how we implement it
    ... # TODO

# ── Node 2: plan ──────────────────────────────────────────────---------------
'''
    As I see it, this is for collecting the vision-based coord mapping &
    text-only DOM for the next node by just hitting both AI with the OG query
    (and potentially the plan, or even information from the database to catch
    disruptions, e.g. popus, 404s). Its main point is to notice if there's a
    disruption and verify that the AI are mostly in agreement, or close to.
    
    Might split the node into one for VBC & one for DOM (AKA one for each AI).
    
    RE: plan/database as arguments, may be good to add for context, but we
    then have to figure the function overload, shape of the query & how to
    format either option.
    
    RE: verification, may create an extra node or cut it out and double down
    in next node instead of verifying within this node.
    
'''
async def plan(query: str) -> str:
    ... # TODO

# ── Node 3: analyse ──────────────────────────────────────────────────────────
'''
    Takes query and updated response to query (or specific step, still not
    clear on that detail), confirms (re-verifies) consensus and checks if
    approval is needed (based on user preference and verification result).
    
    Assume we choose 1 of the two AI for this node.
'''
async def analyse(query: str, perception_data: str) -> str:
    ... # TODO
    
def ask_approval(question: str) -> bool:
    # user input
    ... # TODO
    
# ── Node 4: execute ──────────────────────────────────────────────────────────
'''
    Self-explanatory, no AI, just browser control and a defined error class.
    
    Might just be a function instead, need to revisit what defines a node.
'''
async def execute(query: str, perception_data: str) -> str:
    ... # TODO
    
# ── Node 5: resolve ──────────────────────────────────────────────────────────
'''
    Primarily saving information to the database and training data. Might need
    AI to intuit what it should save? I don't know about that.
    
    Also, depending on how the edges work, this node may define if the
    graph goes to END now.
'''
async def resolve(query: str, success: str) -> str:
    ... # TODO

# ── LangGraph workflow ───────────────────────────────────────────────────────
    
async def node_respond(state: PipelineState) -> Command[Literal["plan", "__end__"]]:
    # LLM + user input (it is its own loop)
    ... # TODO

async def node_plan(state: PipelineState) -> PipelineState:
    # LMM
    ... # TODO

async def node_analyse(state: PipelineState) -> Command[Literal["respond", "execute"]]:
    # LLM
    ... # TODO

async def node_execute(state: PipelineState) -> PipelineState:
    # action
    ... # TODO
    
async def node_resolve(state: PipelineState) -> Command[Literal["plan", "__end__"]]:
    # data
    ... # TODO
    
# TODO: define status & default error handler
# START: error handling placeholder
class State(TypedDict):
    status: str

def default_error_handler(state: State, error: NodeError) -> State:
    return {"status": f"handled: {error.error}"}
# END: error handling placeholder

def build_workflow():
    g: StateGraph[PipelineState] = StateGraph(PipelineState)
    
    g.set_node_defaults(
        retry_policy=RetryPolicy(max_attempts=3),
        error_handler=default_error_handler,
        timeout=TimeoutPolicy(run_timeout=30),
    )
    
    g.add_node("respond", node_respond)
    g.add_node("plan", node_plan)
    g.add_node("analyse", node_analyse)
    g.add_node("execute", node_execute)
    g.add_node("resolve", node_resolve)
    
    # conditional edges return the edge they go to
    g.add_edge("START", "respond")
    g.add_edge("plan", "analyse")
    g.add_edge("execute", "resolve")
    memory = InMemorySaver()
    return g.compile(checkpointer=memory)

async def run_pipeline(query: str):
    # TODO: connect to main properly once it is finished
    workflow = build_workflow()
    config: RunnableConfig = {"configurable": {"thread_id": "1"}}
    await workflow.invoke({"query": query}, config)

if __name__ == "__main__":
    # TODO: function (or inline code) that connects to the UI and takes in input, gets query, gives query to pipeline, loop
    asyncio.run(run_pipeline(""))