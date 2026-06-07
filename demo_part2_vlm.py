import asyncio
import base64
import json
import time
from playwright.async_api import async_playwright
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI()

def encode_image(image_bytes):
    """Converts the Playwright screenshot bytes into a base64 string for OpenAI."""
    return base64.b64encode(image_bytes).decode('utf-8')

async def vlm_agent_loop(page, objective: str):
    print(f"🎯 Objective: {objective}\n")
    
    step = 0
    max_failsafe = 50
    
    while True:
        step += 1
        print(f"--- Step {step} ---")
        
        # 🚨 The Seatbelt
        if step >= max_failsafe:
            print("❌ EMERGENCY STOP: Agent hit the 50-step limit. Task failed.")
            break
            
        # 1. Take a screenshot of the current page state
        screenshot_bytes = await page.screenshot()
        base64_image = encode_image(screenshot_bytes)
        
        # 2. Ask GPT-4o (Vision) what to do next
        print("🧠 Thinking...")
        start_time = time.perf_counter()
        
        response = await client.chat.completions.create(
            model="gpt-4o",
            response_format={ "type": "json_object" },
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a visual web automation agent. You will be given a screenshot of a webpage and an objective. "
                        "You do NOT have access to the HTML. You must determine the next action based strictly on the image. "
                        "You must respond in strict JSON format. "
                        "CRITICAL: You must always include a 'thought_process' key explaining exactly what you see and why you are choosing the next action BEFORE you output the 'action' key.\n"
                        "Valid actions:\n"
                        "1. {\"thought_process\": \"string\", \"action\": \"goto\", \"url\": \"string\"}\n"
                        "2. {\"thought_process\": \"string\", \"action\": \"click\", \"x\": integer, \"y\": integer}\n"
                        "3. {\"thought_process\": \"string\", \"action\": \"type\", \"text\": \"string\"}\n"
                        "4. {\"thought_process\": \"string\", \"action\": \"press_enter\"}\n"
                        "5. {\"thought_process\": \"string\", \"action\": \"done\", \"answer\": \"extracted data\"}"
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Objective: {objective}. What is the next exact JSON action?"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ]
        )
        
        think_time = time.perf_counter() - start_time
        
        # 3. Parse the AI's decision
        try:
            decision = json.loads(response.choices[0].message.content)
            
            # Print the AI's internal monologue
            thoughts = decision.get("thought_process", "No thoughts provided.")
            print(f"\n🤔 AI Thinking: {thoughts}")
            
            # Print the actual action it decided to take
            action_json = {k: v for k, v in decision.items() if k != "thought_process"}
            print(f"🤖 AI Action (took {think_time:.2f}s): {json.dumps(action_json)}")
            
        except Exception as e:
            print(f"❌ Failed to parse JSON: {response.choices[0].message.content}")
            break
        # 4. Execute the physical action via Playwright
        action = decision.get("action")
        
        if action == "goto":
            print(f"🌐 Navigating to {decision['url']}")
            await page.goto(decision["url"])
            await asyncio.sleep(3) # Wait for initial page load

        elif action == "click":
            print(f"🖱️ Clicking at ({decision['x']}, {decision['y']})")
            await page.mouse.click(decision["x"], decision["y"])
            await asyncio.sleep(2) # Wait for UI to react
            
        elif action == "type":
            print(f"⌨️ Typing: '{decision['text']}'")
            await page.keyboard.type(decision["text"])
            await asyncio.sleep(1)
            
        elif action == "press_enter":
            print("↩️ Pressing Enter")
            await page.keyboard.press("Enter")
            await asyncio.sleep(4) # Wait for search results to load
            
        elif action == "done":
            # ✅ This is the ONLY intended way the loop ends natively
            print(f"\n✅ Task Complete! Answer: {decision.get('answer')}")
            break

async def main():
    async with async_playwright() as p:
        # Launch browser so you can watch it happen
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800} # Keep this locked so coordinates stay consistent
        )
        page = await context.new_page()
        
        # Start at a neutral search engine
        await page.goto("https://duckduckgo.com")
        await asyncio.sleep(2)
        
        # Run our custom agent
        objective = "Go to amazon.com and search for spoons, tell me the price of the first result."
        
        # Measure total agent runtime
        start_total = time.perf_counter()
        await vlm_agent_loop(page, objective)
        end_total = time.perf_counter()
        
        print("--------------------------------------------------")
        print(f"⏱️ Total Agent Execution Time: {end_total - start_total:.2f} seconds")
        print("--------------------------------------------------")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())