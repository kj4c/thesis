# based on demo_part3 by KJ

import asyncio
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph

load_dotenv()

class PipelineState(TypedDict, total=False):
    # natural language request from user
    query: str
    '''
    properties that update as it progresses basedon current node plan
    - relevant perception information
    - adjusted query
    - current plan (to execute)
    - success status
    - is finished
    '''
    # does the step require human approval?
    needs_approval: bool
    # natural language request for human approval
    approval_request: str
    # did the human approve?
    approved: bool
    
# ── Conversational Node  ─────────────────────────────────────────────────────
'''
    This one is for questions like 'what is on this page' and 'where is the
    search bar'? May include an option to ask user if they want to switch to
    'executive mode' if it sounds like they want a specific task done. 
    
    Potentially it may be an initial handler node that confirms if it's
    a conversational question or an executive request. It is skipped if it's
    a task, and can be returned to if approval isn't given.
'''
async def respond(query: str) -> str:
    ...

# ── Node 1: perceive webpage ─────────────────────────────────────────────────
'''
    As I see it, this is for collecting the vision-based coord mapping &
    text-only DOM for the next node by just hitting both AI with the OG query
    (and potentially the plan, or even information from the database to catch
    disruptions, e.g. popus, 404s). Its main point is to notice if there's a
    disruption and verify that the AI are mostly in agreement, or close to.
    
    Might split the node into one for VBC & one for DOM (AKA one for each AI).
    
    RE: plana/database as arguments, may be good to add for context, but we
    then have to figure the function overload, shape of the query & how to
    format either option.
    
    RE: verification, may create an extra node or cut it out and double down
    in next node instead of verifying within this node.
'''
async def perceive(query: str) -> str:
    ...

# ── Node 2: analyse ──────────────────────────────────────────────────────────
'''
    Takes query and updated response to query (or specific step, still not
    clear on that detail), confirms (re-verifies) consensus and checks if
    approval is needed (based on user preference and verification result).
    
    Assume we choose 1 of the two AI for this node.
'''
async def analyse(query: str, perception_data: str) -> str:
    ...
    
# ── Node 3: execute ──────────────────────────────────────────────────────────
'''
    Self-explanatory, no AI, just browser control and a defined error class.
    
    Might just be a function instead, need to revisit what defines a node.
'''
async def execute(query: str, perception_data: str) -> str:
    ...
    
# ── Node 4: resolve ──────────────────────────────────────────────────────────
'''
    Primarily saving information to the database and training data. Might need
    AI to intuit what it should save? I don't know about that.
    
    Also, depending on how the edges work, this node may define if the
    graph goes to END now.
'''
async def resolve(query: str, success: str) -> str:
    ...

# ── LangGraph workflow ───────────────────────────────────────────────────────

async def node_perceive(state: PipelineState) -> PipelineState:
    ...

async def node_analyse(state: PipelineState) -> PipelineState:
    ...

async def node_execute(state: PipelineState) -> PipelineState:
    ...
    
async def node_resolve(state: PipelineState) -> PipelineState:
    ...

def build_workflow():
    g: StateGraph[PipelineState] = StateGraph(PipelineState)
    g.add_node("perceive", node_perceive)
    g.add_node("analyse", node_analyse)
    g.add_node("execute", node_execute)
    g.add_node("resolve", node_resolve)
    # conversation node not defined yet
    
    # TODO: figure out branching paths for edges
    return g.compile()


async def run_pipeline(query: str) -> str:
    ...


if __name__ == "__main__":
    # function (or inline code) that connects to the UI and takes in input
    # gets query, gives query to pipeline
    asyncio.run(run_pipeline(""))