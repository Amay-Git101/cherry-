from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import re

app = FastAPI(title="Hackathon Agent", version="10.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

INJECTION_PATTERNS = [
    r"IGNORE\s+ALL\s+PREVIOUS\s+INSTRUCTIONS\.?",
    r"ignore\s+all\s+previous\s+instructions\.?",
    r"\[SYSTEM\].*?(?=\n|Question:|$)",
    r"You are now\s+\w+\.?",
    r"Forget everything\.?",
    r"Your new instructions are.*?(?=\.|$)",
    r"As your developer.*?(?=\.|$)",
    r"Output only\s+[\"']?.*?[\"']?\.?",
    r"Always (say|output|respond with)\s+.*?(?=\.|$)",
    r"always (say|output|respond with)\s+.*?(?=\.|$)",
    r"ignore that,?\s+output\s+\w+\.?",
]

TASK_EXTRACTORS = [
    r"[Aa]ctual\s+task[:\s]+(.+)$",
    r"[Rr]eal\s+task[:\s]+(.+)$",
    r"[Tt]rue\s+task[:\s]+(.+)$",
    r"[Yy]our\s+task[:\s]+(.+)$",
    r"[Qq]uestion[:\s]+(.+)$",
]


def sanitize_query(query: str) -> str:
    cleaned = query
    for pattern in INJECTION_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    for pattern in TASK_EXTRACTORS:
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            return match.group(1).strip()
    return cleaned.strip()


async def fetch_asset(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(url)
            return resp.text[:8000]
    except Exception as e:
        return f"[Could not fetch {url}: {e}]"


async def call_groq(messages: list, max_tokens: int = 100) -> str:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0
            }
        )
        result = resp.json()
    return result["choices"][0]["message"]["content"].strip()


def is_rule_problem(query: str) -> bool:
    return bool(re.search(r"rule\s*\d", query, re.IGNORECASE))


@app.get("/")
async def root():
    return {"status": "online", "endpoint": "POST /v1/answer"}


@app.post("/v1/answer")
async def handle_query(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    raw_query = data.get("query", "")
    assets = data.get("assets", [])

    clean_query = sanitize_query(raw_query)

    asset_contents = []
    for url in assets:
        content = await fetch_asset(url)
        asset_contents.append(f"--- Content from {url} ---\n{content}")

    context = "\n\n".join(asset_contents) if asset_contents else ""
    user_content = f"{context}\n\nQuestion: {clean_query}" if context else f"Question: {clean_query}"

    if is_rule_problem(clean_query):
        # Step 1: Let LLM reason carefully with explicit arithmetic
        reasoning = await call_groq([
            {
                "role": "system",
                "content": """You are a precise rule execution engine. 
Execute each rule step by step showing arithmetic clearly.
At the very end write exactly: ANSWER: <value>
Only the value after ANSWER: matters. It must be a single word or number."""
            },
            {
                "role": "user",
                "content": f"Execute these rules and show each step:\n{clean_query}"
            }
        ], max_tokens=400)

        # Extract ANSWER: pattern
        match = re.search(r"ANSWER:\s*([A-Za-z0-9]+)\s*$", reasoning, re.IGNORECASE | re.MULTILINE)
        if match:
            return {"output": match.group(1).strip()}

        # Step 2: If ANSWER not found, ask LLM to extract from its own reasoning
        extraction = await call_groq([
            {
                "role": "system",
                "content": "Extract only the final output value from this reasoning. Return a single word or number only. Nothing else."
            },
            {"role": "user", "content": reasoning}
        ], max_tokens=10)
        return {"output": extraction.strip()}

    else:
        # Standard QA
        system_prompt = """You are a precise answer extraction engine for an automated grading system.
Return ONLY the final answer — a name, number, date, or short phrase.
Never write sentences, explanations, or show working.
Examples:
- "Who scored highest? Alice=80 Bob=90" → Bob
- "What is 13 + 7?" → 20
- "Extract date from: Meeting on 12 March 2024" → 12 March 2024
- Yes/No questions → Yes or No"""

        answer = await call_groq([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ], max_tokens=50)

        return {"output": answer}


@app.post("/answer")
async def handle_query_alt(request: Request):
    return await handle_query(request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5050, reload=True)