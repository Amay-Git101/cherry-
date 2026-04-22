from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import re

app = FastAPI(title="Hackathon Agent", version="12.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ARROW = "(?:\u2192|->|=>)"   # → -> =>

# ── Injection defence ─────────────────────────────────────────────────────────

INJECTION_PATTERNS = [
    r"IGNORE\s+ALL\s+PREVIOUS\s+INSTRUCTIONS\.?",
    r"ignore\s+all\s+previous\s+instructions\.?",
    r"\[SYSTEM\].*?(?=\n|Question:|$)",
    r"You are now\s+\w+\.?", r"Forget everything\.?",
    r"Your new instructions are.*?(?=\.|$)",
    r"As your developer.*?(?=\.|$)",
    r"Output only\s+[\"']?.*?[\"']?\.?",
    r"Always (say|output|respond with)\s+.*?(?=\.|$)",
    r"always (say|output|respond with)\s+.*?(?=\.|$)",
    r"ignore that,?\s+output\s+\w+\.?",
]
TASK_EXTRACTORS = [
    r"[Aa]ctual\s+task[:\s]+(.+)$", r"[Rr]eal\s+task[:\s]+(.+)$",
    r"[Tt]rue\s+task[:\s]+(.+)$",   r"[Yy]our\s+task[:\s]+(.+)$",
    r"[Qq]uestion[:\s]+(.+)$",
]

def sanitize_query(query: str) -> str:
    cleaned = query
    for p in INJECTION_PATTERNS:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    for p in TASK_EXTRACTORS:
        m = re.search(p, cleaned, re.DOTALL)
        if m:
            return m.group(1).strip()
    return cleaned.strip()


# ── Deterministic rule executor (NO LLM) ─────────────────────────────────────

def is_rule_problem(query: str) -> bool:
    return bool(re.search(r"rule\s*\d", query, re.IGNORECASE))

def extract_input_number(query: str):
    for pat in [
        r"input\s+(?:number|value|is)?\s*[:\-]?\s*(-?\d+(?:\.\d+)?)",
        r"(?:number|value|starting)\s+(?:is\s+)?(-?\d+(?:\.\d+)?)",
        r"apply\s+rules.*?(?:to|on)\s+(?:number\s+)?(-?\d+(?:\.\d+)?)",
        r"(?:^|\s)(-?\d+(?:\.\d+)?)\s*$",
    ]:
        m = re.search(pat, query, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None

def evaluate_condition(value: float, cond: str) -> bool:
    t = re.sub(r"\b(final|result|the|value|number)\b", "", cond.lower()).strip().rstrip(".")
    m = re.search(r"(?:divisible\s+by|multiple\s+of)\s+(-?\d+(?:\.\d+)?)", t)
    if m:
        n = float(m.group(1)); return n != 0 and value % n == 0
    if re.search(r"\beven\b", t):     return int(value) % 2 == 0
    if re.search(r"\bodd\b", t):      return int(value) % 2 != 0
    if re.search(r"\bpositive\b", t): return value > 0
    if re.search(r"\bnegative\b", t): return value < 0
    if re.search(r"\bzero\b", t):     return value == 0
    m = re.search(r"(>=|<=|>|<|==|=)\s*(-?\d+(?:\.\d+)?)", t)
    if m:
        op, n = m.group(1), float(m.group(2))
        return {">=": value >= n, "<=": value <= n, ">": value > n,
                "<": value < n, "==": value == n, "=": value == n}[op]
    return False

def apply_action(value: float, action: str) -> float:
    t = action.strip().lower().rstrip(". ")
    if re.search(r"\bdouble\b", t):  return value * 2
    if re.search(r"\btriple\b", t):  return value * 3
    if re.search(r"\bhalve\b|\bdivide\s+by\s+2\b", t): return value / 2
    if re.search(r"\bsquare\b", t):  return value * value
    if re.search(r"\bnegate\b", t):  return -value
    for pat, fn in [
        (r"add\s+(-?\d+(?:\.\d+)?)",             lambda n: value + n),
        (r"subtract\s+(-?\d+(?:\.\d+)?)",         lambda n: value - n),
        (r"multiply\s+(?:by\s+)?(-?\d+(?:\.\d+)?)", lambda n: value * n),
        (r"divide\s+(?:by\s+)?(-?\d+(?:\.\d+)?)", lambda n: value / n if n else value),
        (r"(?:set\s+to|becomes?)\s+(-?\d+(?:\.\d+)?)", lambda n: n),
    ]:
        m = re.search(pat, t)
        if m:
            return fn(float(m.group(1)))
    return value

def execute_rules(query: str):
    value = extract_input_number(query)
    if value is None:
        return None
    blocks = re.split(r"Rule\s+\d+\s*[:\-]", query, flags=re.IGNORECASE)
    rules  = [b.strip() for b in blocks[1:] if b.strip()]
    if not rules:
        return None

    for rule in rules:
        # Terminal output rule: "If <cond> → output LABEL. Otherwise → output <fallback>."
        om = re.search(rf"(?:if|when)?\s*(.+?)\s*{ARROW}\s*(?:output|return|say|print)\s+([A-Za-z0-9]+)", rule, re.IGNORECASE)
        ow = re.search(rf"otherwise\s*{ARROW}\s*(?:output|return|say|print)\s+(.+?)(?:\.|$)", rule, re.IGNORECASE)
        if om and ow:
            label    = om.group(2).strip()
            fallback = ow.group(1).strip().rstrip(".")
            if evaluate_condition(value, om.group(1)):
                return label
            if re.search(r"\bthe\s+(?:number|result|value)\b", fallback, re.IGNORECASE):
               ()
            return fallback

        # Transformation rule: "If <cond> → <action>. [If ...] [Otherwise → <action>.]"
        branches = list(re.finditer(
            rf"\bif\b\s+(.+?)\s*{ARROW}\s*(.+?)(?=\.\s*(?:\bif\b|\botherwise\b)|$)",
            rule, re.IGNORECASE | re.DOTALL))
        otherwise = re.search(rf"\botherwise\b\s*{ARROW}\s*(.+?)(?=\.|$)", rule, re.IGNORECASE)

        matched = False
        for b in branches:
            if evaluate_condition(value, b.group(1)):
                value = apply_action(value, b.group(2))
                matched = True
                break
        if not matched and otherwise:
            value = apply_action(value, otherwise.group(1))

    return str(int(value) if value == int(value) else value)


# ── LLM (QA only) ─────────────────────────────────────────────────────────────

async def fetch_asset(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            return r.text[:8000]
    except Exception as e:
        return f"[Could not fetch {url}: {e}]"

async def call_groq(messages: list, max_tokens: int = 100) -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": messages,
                  "max_tokens": max_tokens, "temperature": 0})
        return r.json()["choices"][0]["message"]["content"].strip()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "online", "version": "12.0.0", "endpoint": "POST /v1/answer"}

@app.post("/v1/answer")
async def handle_query(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    clean_query = sanitize_query(data.get("query", ""))
    assets      = data.get("assets", [])

    # Rule problems: pure Python, zero network calls, instant response
    if is_rule_problem(clean_query):
        result = execute_rules(clean_query)
        if result is not None:
            return {"output": result}
        # Should never reach here on well-formed input, but just in case:
        return {"output": "0"}

    # QA problems: LLM with assets if provided
    asset_parts = []
    for url in assets:
        asset_parts.append(f"--- {url} ---\n{await fetch_asset(url)}")
    context = "\n\n".join(asset_parts)
    user_content = f"{context}\n\nQuestion: {clean_query}" if context else f"Question: {clean_query}"

    answer = await call_groq([
        {"role": "system", "content": (
            "You are a precise answer extraction engine for an automated grading system.\n"
            "Return ONLY the final answer — a name, number, date, or short phrase.\n"
            "Never write sentences, explanations, or show working.\n"
            "Examples:\n"
            '- "Who scored highest? Alice=80 Bob=90" -> Bob\n'
            '- "What is 13 + 7?" -> 20\n'
            '- "Extract date: Meeting on 12 March 2024" -> 12 March 2024\n'
            "- Yes/No questions -> Yes or No"
        )},
        {"role": "user", "content": user_content},
    ], max_tokens=50)
    return {"output": answer}

@app.post("/answer")
async def handle_query_alt(request: Request):
    return await handle_query(request)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)# force-rebuild-Wed, Apr 22, 2026  3:57:07 PM
