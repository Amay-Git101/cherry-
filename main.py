from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import re

app = FastAPI()


def is_rule_problem(query: str) -> bool:
    return "rule" in query.lower()


def extract_number(query: str) -> int:
    match = re.search(r"input number (\d+)", query.lower())
    return int(match.group(1))


def apply_operation(value, operation: str):
    operation = operation.lower().strip()

    if "double" in operation:
        return value * 2

    if "add" in operation:
        num = int(re.search(r"add (\d+)", operation).group(1))
        return value + num

    if "subtract" in operation:
        num = int(re.search(r"subtract (\d+)", operation).group(1))
        return value - num

    if "multiply" in operation:
        num = int(re.search(r"multiply by (\d+)", operation).group(1))
        return value * num

    if "divide" in operation:
        num = int(re.search(r"divide by (\d+)", operation).group(1))
        return value // num

    return value


def check_condition(value, condition: str):
    condition = condition.lower().strip()

    if "even" in condition:
        return value % 2 == 0

    if "odd" in condition:
        return value % 2 != 0

    if ">" in condition:
        num = int(re.search(r">\s*(\d+)", condition).group(1))
        return value > num

    if "<" in condition:
        num = int(re.search(r"<\s*(\d+)", condition).group(1))
        return value < num

    if "divisible by" in condition:
        num = int(re.search(r"divisible by (\d+)", condition).group(1))
        return value % num == 0

    return False


def parse_rules(query: str):
    # Split rules
    rules = re.split(r"rule\s*\d+:", query.lower())[1:]
    return [r.strip() for r in rules]


def execute_rules(query: str):
    value = extract_number(query)
    rules = parse_rules(query)

    for rule in rules:
        # Handle final output rule separately
        if "output" in rule:
            if "divisible by" in rule:
                num = int(re.search(r"divisible by (\d+)", rule).group(1))
                word = re.search(r'output\s+"?([a-zA-Z0-9]+)"?', rule).group(1)

                if value % num == 0:
                    return word
                else:
                    return str(value)

        # General IF-ELSE rule
        parts = re.split(r"if|otherwise", rule)

        if len(parts) >= 2:
            condition_part = parts[1]
            true_action = re.search(r"→\s*(.*?)\.", rule)
            false_action = re.search(r"otherwise\s*→\s*(.*?)\.", rule)

            condition_match = re.search(r"if (.*?) →", rule)
            if condition_match:
                condition = condition_match.group(1)

                if check_condition(value, condition):
                    value = apply_operation(value, true_action.group(1))
                elif false_action:
                    value = apply_operation(value, false_action.group(1))

    return str(value)


@app.get("/")
def root():
    return {"status": "online"}


@app.post("/v1/answer")
async def handle_query(request: Request):
    try:
        data = await request.json()
    except:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    query = data.get("query", "")

    if is_rule_problem(query):
        result = execute_rules(query)
        return {"output": result}

    return {"output": ""}