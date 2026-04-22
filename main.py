from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os

app = FastAPI(title="Hackathon Agent", version="4.0.0")

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

    # Fetch asset URLs if provided
    asset_contents = []
    for url in assets:
        content = await fetch_asset(url)
        asset_contents.append(f"--- Content from {url} ---\n{content}")

    context = "\n\n".join(asset_contents) if asset_contents else ""

    system_prompt = """You are an answer extraction engine for an automated grading system.

Return ONLY the answer — nothing else.

RULES:
- Never write full sentences
- Never say "The answer is..." or "Based on..." or any preamble
- Never explain your reasoning
- If asked who scored highest → just the name e.g. "Bob"
- If asked to extract a date → just the date e.g. "12 March 2024"
- If asked a yes/no question → just "Yes" or "No"
- If asked for a number → just the number e.g. "25"
- Match the exact format of what is being asked for

One or two words maximum unless the answer genuinely requires more."""

    user_content = f"{context}\n\nQuestion: {query}" if context else f"Question: {query}"

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "max_tokens": 50,
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
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)