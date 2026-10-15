import base64
from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from openai import OpenAI
import tempfile
import os
from dotenv import load_dotenv

# 👈 IMPORT EXACTLY LIKE YOUR WORKING DEMO
from browser_use import Agent, ChatOpenAI

load_dotenv()

app = FastAPI()
client = OpenAI()

class BrainPlan(BaseModel):
    agent_task: str
    reasoning: str

@app.post("/api/v1/process-intent")
async def process_intent(
    audio: UploadFile = File(...),
    image: UploadFile = File(...)
):
    print("📥 Incoming package from iOS...")

    # 1. Transcribe Voice
    with tempfile.NamedTemporaryFile(delete=False, suffix=".m4a") as temp_audio:
        temp_audio.write(await audio.read())
        temp_audio_path = temp_audio.name

    with open(temp_audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file
        )
    os.remove(temp_audio_path)
    user_prompt = transcription.text
    print(f"🗣️ User said: {user_prompt}")

    # 2. Encode Image
    image_bytes = await image.read()
    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    # 3. Translating vision + voice into a web browser task
    print("🧠 Translating vision + voice into a web browser task...")
    response = client.beta.chat.completions.parse(
        model="gpt-4o-2024-08-06",
        messages=[
            {
                "role": "system", 
                "content": "You are a bridge between smart glasses and an autonomous web agent. Look at the image and the user's voice command. Write a single, highly specific instruction for the web agent."
            },
            {"role": "user", "content": [
                {"type": "text", "text": f"User said: {user_prompt}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]}
        ],
        response_format=BrainPlan
    )
    
    plan = response.choices[0].message.parsed
    print(f"🎯 Web Agent Objective: {plan.agent_task}")

    # 4. Launching the browser agent
    print("🚀 Launching the browser agent...")
    
    # 👈 Initialize EXACTLY like your working demo
    llm = ChatOpenAI(model="gpt-4o") 
    
    agent = Agent(
        task=plan.agent_task,
        llm=llm,
    )
    
    history = await agent.run()
    final_result = history.final_result()
    
    print(f"✅ Task Finished! Final Output: {final_result}")

    return {
        "status": "success",
        "agent_task_executed": plan.agent_task,
        "final_result": final_result
    }