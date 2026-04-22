from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _eval_cond(cond: str, value: float) -> bool:
    if "even" in cond: return int(value) % 2 == 0
    if "odd" in cond: return int(value) % 2 != 0
    m = re.search(r"([><!]=?)\s*(-?\d+)", cond)
    if m:
        op, num = m.group(1), float(m.group(2))
        return {">": value > num, "<": value < num,
                ">=": value >= num, "<=": value <= num}.get(op, False)
    if "divisible by" in cond:
        d = re.search(r"divisible\s+by\s+(\d+)", cond)
        if d: return int(value) % int(d.group(1)) == 0
    return False


def _apply_action(action: str, value: float) -> float:
    action = action.strip().rstrip(".")
    if "double" in action:  return value * 2
    if "triple" in action:  return value * 3
    if "square" in action:  return value ** 2
    if "halve" in action:   return value / 2
    if "negate" in action:  return -value
    m = re.search(r"add\s+(-?\d+(?:\.\d+)?)", action)
    if m: return value + float(m.group(1))
    m = re.search(r"subtract\s+(-?\d+(?:\.\d+)?)", action)
    if m: return value - float(m.group(1))
    m = re.search(r"multiply\s+by\s+(-?\d+(?:\.\d+)?)", action)
    if m: return value * float(m.group(1))
    m = re.search(r"divide\s+by\s+(-?\d+(?:\.\d+)?)", action)
    if m: return value / float(m.group(1))
    return value


def solve_rules(query: str):
    if not re.search(r"rule\s*1", query, re.IGNORECASE):
        return None
    num_match = re.search(r"number\s+(-?\d+)", query, re.IGNORECASE)
    if not num_match:
        return None

    n = float(num_match.group(1))
    blocks = re.split(r"Rule\s*\d+\s*:", query, flags=re.IGNORECASE)
    rule_texts = [b.strip() for b in blocks[1:] if b.strip()]

    for rule in rule_texts:
        rl = rule.lower()

        # OUTPUT RULE — "If result divisible by X → output WORD. Otherwise → output the number."
        out_match = re.search(
            r"if\s+(?:final\s+)?result\s+(?:is\s+)?divisible\s+by\s+(\d+)\s*→\s*output\s+[\"\']*(\w+)",
            rl
        )
        if out_match:
            divisor = int(out_match.group(1))
            fizz_word = out_match.group(2).upper()
            else_match = re.search(r"otherwise\s*→\s*output\s+(?:the\s+)?(\w+)", rl)
            int_n = int(n)
            if int_n % divisor == 0:
                return fizz_word
            if else_match and else_match.group(1).lower() != "number":
                return else_match.group(1).upper()
            return str(int_n)

        # CONDITIONAL RULE — split sentences, use → as action separator
        sentences = re.split(r"\.\s+", rl)
        matched = False
        otherwise_action = None

        for sentence in sentences:
            sentence = sentence.strip().rstrip(".")
            if sentence.startswith("otherwise"):
                ow = re.search(r"otherwise\s*→\s*(.+)", sentence)
                if ow:
                    otherwise_action = ow.group(1).strip()
            elif sentence.startswith("if"):
                parts = sentence.split("→", 1)
                if len(parts) == 2:
                    cond = parts[0].replace("if", "", 1).strip()
                    action = parts[1].strip()
                    if not matched and _eval_cond(cond, n):
                        n = _apply_action(action, n)
                        matched = True

        if not matched and otherwise_action:
            n = _apply_action(otherwise_action, n)

    int_n = int(n) if n == int(n) else n
    return str(int_n)


async def fetch_asset(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(url)
            return resp.text[:8000]
    except Exception as e:
        return f"[Could not fetch {url}: {e}]"


async def call_groq(system: str, user: str, max_tokens: int = 50) -> str:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                "max_tokens": max_tokens,
                "temperature": 0
            }
        )
        return resp.json()["choices"][0]["message"]["content"].strip()


@app.get("/")
async def root():
    return {"status": "online", "version": "11.0.0"}


@app.post("/v1/answer")
async def handle_query(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    query = data.get("query", "")
    assets = data.get("assets", [])

    # 1. Deterministic rule solver
    rule_answer = solve_rules(query)
    if rule_answer is not None:
        return {"output": rule_answer}

    # 2. Fetch assets
    context_parts = []
    for url in assets:
        content = await fetch_asset(url)
        context_parts.append(f"[Source: {url}]\n{content}")
    context = "\n\n".join(context_parts)

    user_msg = f"{context}\n\nQuestion: {query}".strip() if context else f"Question: {query}"

    system = (
        "You are an answer extraction engine for an automated grading system. "
        "Return ONLY the answer — a single word, number, name, or short phrase. "
        "No sentences. No explanation. No punctuation unless part of the answer. "
        "Examples: 'Who scored highest, Alice 80 or Bob 90?' -> Bob | "
        "'What is 13+7?' -> 20 | "
        "'Extract date from Meeting on 12 March 2024' -> 12 March 2024 | "
        "Yes/No questions -> Yes or No"
    )

    answer = await call_groq(system, user_msg)
    return {"output": answer}


@app.post("/answer")
async def handle_alt(request: Request):
    return await handle_query(request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)