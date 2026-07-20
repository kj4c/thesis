import asyncio
import time
from browser_use import Agent, ChatGoogle, ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

async def run_baseline_demo():
    print("🚀 [DEMO START] Initiating Baseline Test with GPT-4o-Mini...")
    
    # 1. Initialize the patched frontier model
    llm = ChatGoogle(model="gemini-2.5-flash")
    # llm = ChatOpenAI(model="gpt-5-nano")
    
    task_prompt = (
        "Go to amazon and find me the first price you see after searching up spoons"
    )
    
    print(f"🎯 Objective: {task_prompt}\n")
    
    # 3. Spin up the agent
    agent = Agent(
        task=task_prompt,
        llm=llm,
    )

    start_time = time.perf_counter()
    
    # 4. Execute and measure
    history = await agent.run()

    end_time = time.perf_counter()
    
    execution_time = end_time - start_time
    
    print("\n✅ Task Finished!")
    print(f"Final Output: {history.final_result()}")
    print("--------------------------------------------------")
    print(f"⏱️ Total Execution Time: {execution_time:.2f} seconds") # Prints exactly like: 14.53 seconds
    print("--------------------------------------------------")

if __name__ == "__main__":
    asyncio.run(run_baseline_demo())