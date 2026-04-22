from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import re

app = FastAPI(title="Hackathon Agent", version="11.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Arrow variants: → (U+2192)  ->  =>
ARROW_PAT = r"(?:\u2192|->|=>)"

# ─── Prompt Injection Defence ─────────────────────────────────────────────────

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


# ─── Generic Rule Executor ────────────────────────────────────────────────────

def is_rule_problem(query: str) -> bool:
    return bool(re.search(r"rule\s*\d", query, re.IGNORECASE))


def extract_input_number(query: str):
    patterns = [
        r"input\s+(?:number|value|is)?\s*[:\-]?\s*(-?\d+(?:\.\d+)?)",
        r"(?:number|value|starting)\s+(?:is\s+)?(-?\d+(?:\.\d+)?)",
        r"apply\s+rules.*?(?:to|on)\s+(?:number\s+)?(-?\d+(?:\.\d+)?)",
        r"(?:^|\s)(-?\d+(?:\.\d+)?)\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, query, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def evaluate_condition(value: float, condition_text: str) -> bool:
    t = re.sub(r"\b(final|result|the|value|number)\b", "", condition_text.strip().lower())
    t = t.strip().rstrip(".")
    m = re.search(r"(?:divisible\s+by|multiple\s+of)\s+(-?\d+(?:\.\d+)?)", t)
    if m:
        n = float(m.group(1))
        return n != 0 and value % n == 0
    if re.search(r"\beven\b", t):    return int(value) % 2 == 0
    if re.search(r"\bodd\b", t):     return int(value) % 2 != 0
    if re.search(r"\bpositive\b", t): return value > 0
    if re.search(r"\bnegative\b", t): return value < 0
    if re.search(r"\bzero\b", t):    return value == 0
    m = re.search(r"(>=|<=|>|<|==|=)\s*(-?\d+(?:\.\d+)?)", t)
    if m:
        op, n = m.group(1), float(m.group(2))
        return {">=": value >= n, "<=": value <= n, ">": value > n,
                "<": value < n, "==": value == n, "=": value == n}[op]
    return False


def apply_action(value: float, action_text: str) -> float:
    t = action_text.strip().lower().rstrip(". ")
    if re.search(r"\bdouble\b", t):  return value * 2
    if re.search(r"\btriple\b", t):  return value * 3
    if re.search(r"\bhalve\b|\bdivide\s+by\s+2\b", t): return value / 2
    if re.search(r"\bsquare\b", t):  return value * value
    if re.search(r"\bnegate\b|\bmultiply\s+by\s+-1\b", t): return -value
    m = re.search(r"add\s+(-?\d+(?:\.\d+)?)", t)
    if m: return value + float(m.group(1))
    m = re.search(r"subtract\s+(-?\d+(?:\.\d+)?)", t)
    if m: return value - float(m.group(1))
    m = re.search(r"multiply\s+(?:by\s+)?(-?\d+(?:\.\d+)?)", t)
    if m: return value * float(m.group(1))
    m = re.search(r"divide\s+(?:by\s+)?(-?\d+(?:\.\d+)?)", t)
    if m:
        n = float(m.group(1))
        return value / n if n != 0 else value
    m = re.search(r"(?:set\s+to|becomes?)\s+(-?\d+(?:\.\d+)?)", t)
    if m: return float(m.group(1))
    return value


def execute_rules(query: str):
    value = extract_input_number(query)
    if value is None:
        return None

    rule_blocks = re.split(r"Rule\s+\d+\s*[:\-]", query, flags=re.IGNORECASE)
    rule_texts = [b.strip() for b in rule_blocks[1:] if b.strip()]
    if not rule_texts:
        return None

    AP = ARROW_PAT

    for rule_text in rule_texts:
        # Terminal output rule
        output_match = re.search(
            rf"(?:if|when)?\s*(.+?)\s*{AP}\s*(?:output|return|say|print)\s+([A-Za-z0-9]+)",
            rule_text, re.IGNORECASE)
        otherwise_output = re.search(
            rf"otherwise\s*{AP}\s*(?:output|return|say|print)\s+(.+?)(?:\.|$)",
            rule_text, re.IGNORECASE)

        if output_match and otherwise_output:
            cond     = output_match.group(1).strip()
            label    = output_match.group(2).strip()
            fallback = otherwise_output.group(1).strip().rstrip(".")
            if evaluate_condition(value, cond):
                return label
            elif re.search(r"\bthe\s+(?:number|result|value)\b", fallback, re.IGNORECASE):
                return str(int(value) if value == int(value) else value)
            else:
                return fallback

        # Transformation rule
        branch_matches = list(re.finditer(
            rf"\bif\b\s+(.+?)\s*{AP}\s*(.+?)(?=\.\s*(?:\bif\b|\botherwise\b)|$)",
            rule_text, re.IGNORECASE | re.DOTALL))
        otherwise_action = re.search(
            rf"\botherwise\b\s*{AP}\s*(.+?)(?=\.|$)", rule_text, re.IGNORECASE)

        matched = False
        for bm in branch_matches:
            if evaluate_condition(value, bm.group(1)):
                value = apply_action(value, bm.group(2))
                matched = True
                break
        if not matched and otherwise_action:
            value = apply_action(value, otherwise_action.group(1))

    int_val = int(value) if value == int(value) else value
    return str(int_val)


# ─── LLM helpers ──────────────────────────────────────────────────────────────

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
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0,
            },
        )
        result = resp.json()
    return result["choices"][0]["message"]["content"].strip()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "online", "version": "11.0.0", "endpoint": "POST /v1/answer"}


@app.post("/v1/answer")
async def handle_query(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    raw_query = data.get("query", "")
    assets    = data.get("assets", [])

    clean_query = sanitize_query(raw_query)

    asset_contents = []
    for url in assets:
        content = await fetch_asset(url)
        asset_contents.append(f"--- Content from {url} ---\n{content}")
    context = "\n\n".join(asset_contents)
    user_content = f"{context}\n\nQuestion: {clean_query}" if context else f"Question: {clean_query}"

    if is_rule_problem(clean_query):
        result = execute_rules(clean_query)
        if result is not None:
            return {"output": result}

        # Fallback: LLM with ANSWER: extraction
        reasoning = await call_groq(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a precise rule execution engine. "
                        "Execute each rule step by step with explicit arithmetic. "
                        "At the very end write EXACTLY: ANSWER: <value>  "
                        "where <value> is a single word or number only."
                    ),
                },
                {"role": "user", "content": f"Execute these rules:\n{clean_query}"},
            ],
            max_tokens=400,
        )
        m = re.search(r"ANSWER:\s*([A-Za-z0-9]+)\s*$", reasoning, re.IGNORECASE | re.MULTILINE)
        if m:
            return {"output": m.group(1).strip()}

        extraction = await call_groq(
            [
                {"role": "system", "content": "Extract only the final output value. Return a single word or number ONLY."},
                {"role": "user", "content": reasoning},
            ],
            max_tokens=10,
        )
        return {"output": extraction.strip()}

    # Default strict QA
    system_prompt = (
        "You are a precise answer extraction engine for an automated grading system.\n"
        "Return ONLY the final answer — a name, number, date, or short phrase.\n"
        "Never write sentences, explanations, or show working.\n"
        "Examples:\n"
        '- "Who scored highest? Alice=80 Bob=90" -> Bob\n'
        '- "What is 13 + 7?" -> 20\n'
        '- "Extract date from: Meeting on 12 March 2024" -> 12 March 2024\n'
        "- Yes/No questions -> Yes or No"
    )
    answer = await call_groq(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=50,
    )
    return {"output": answer}


@app.post("/answer")
async def handle_query_alt(request: Request):
    return await handle_query(request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)# v11
