import asyncio
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
import sys
import os

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.inference.streaming_client import stream_ollama_chat

app = FastAPI()

# State to simulate drops
drop_count = 0

@app.post("/api/chat")
async def mock_chat(request: Request):
    global drop_count
    data = await request.json()
    messages = data.get("messages", [])
    
    # Check if this is a retry (has the system instruction)
    is_retry = any("[SYSTEM: Continue" in m.get("content", "") for m in messages)
    
    async def generate():
        global drop_count
        if not is_retry:
            # First attempt: yield 5 tokens then DIE
            for i in range(5):
                yield json.dumps({"message": {"content": f"Token_{i} "}}) + "\n"
                await asyncio.sleep(0.1)
            
            print("!!! SIMULATING CONNECTION DROP !!!")
            # To simulate a hard drop, we just stop yielding and the connection will time out or we can raise an error
            # But the client expects a drop. We can't easily kill the server from inside the handler, 
            # but we can return nothing or close the stream.
            return 
        else:
            # Retry attempt: yield the rest
            yield json.dumps({"message": {"content": "RECOVERED_SUCCESSFULLY"}}) + "\n"
            yield json.dumps({"done": True}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")

async def run_test():
    print("Starting Mock Ollama Server...")
    config = uvicorn.Config(app, host="127.0.0.1", port=11435, log_level="error")
    server = uvicorn.Server(config)
    
    # Run server in background
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1) # wait for start
    
    print("Running D5 Resiliency Test...")
    tokens = []
    warnings = []
    success = False
    
    try:
        async for event in stream_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
            ollama_url="http://127.0.0.1:11435",
            max_retries=2
        ):
            if event["type"] == "token":
                tokens.append(event["content"])
                print(f"Received: {event['content']}")
            elif event["type"] == "warning":
                warnings.append(event["message"])
                print(f"Warning: {event['message']}")
            elif event["type"] == "done":
                success = True
                print(f"Done! Retries: {event.get('retries')}, Total Tokens: {event.get('total_tokens')}")
    except Exception as e:
        print(f"Test Failed with Error: {e}")
    finally:
        server.should_exit = True
        await server_task

    # Validations
    print("\n--- TEST RESULTS ---")
    print(f"Tokens: {''.join(tokens)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Success: {success}")
    
    if success and len(warnings) > 0 and "RECOVERED_SUCCESSFULLY" in "".join(tokens):
        print("\n✅ D5 VERIFICATION PASSED: Tokens preserved across connection drop!")
        return True
    else:
        print("\n❌ D5 VERIFICATION FAILED.")
        return False

if __name__ == "__main__":
    asyncio.run(run_test())
