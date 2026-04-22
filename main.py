from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os

app = FastAPI(title="Hackathon Level 1 Agent", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


async def fetch_asset(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(url)
            return resp.text[:8000]
    except Exception as e:
        return f"[Could not fetch {url}: {e}]"


@app.get("/")
async def root():
    return {"status": "online", "endpoint": "POST /v1/answer"}


@app.post("/v1/answer")
async def handle_query(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    query = data.get("query", "")
    assets = data.get("assets", [])

    # Fetch all asset URLs
    asset_contents = []
    for url in assets:
        content = await fetch_asset(url)
        asset_contents.append(f"--- Content from {url} ---\n{content}")

    context = "\n\n".join(asset_contents) if asset_contents else "No assets provided."

    messages = [
        {
            "role": "system",
            "content": (
                "You are a precise question-answering agent for a hackathon evaluation system. "
                "You will be given a question and reference content fetched from URLs. "
                "Answer the question as accurately and concisely as possible. "
                "Return ONLY the answer — no preamble, no explanation, no markdown formatting."
            )
        },
        {
            "role": "user",
            "content": f"Reference Content:\n{context}\n\nQuestion: {query}\n\nAnswer:"
        }
    ]

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama3-70b-8192",
                "messages": messages,
                "max_tokens": 512,
                "temperature": 0
            }
        )
        result = resp.json()

    answer = result["choices"][0]["message"]["content"].strip()
    return {"output": answer}


@app.post("/answer")
async def handle_query_alt(request: Request):
    return await handle_query(request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)