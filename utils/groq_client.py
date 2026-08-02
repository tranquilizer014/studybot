import base64, json, re, logging, asyncio
from groq import AsyncGroq
from config import GROQ_API_KEYS

logger = logging.getLogger(__name__)
_current_key_index = 0
_clients = [AsyncGroq(api_key=k) for k in GROQ_API_KEYS]

def _parse_json(text):
    text = re.sub(r'```json|```', '', text).strip()
    m = re.search(r'\{.*\}', text, re.DOTALL)
    return json.loads(m.group() if m else text)

async def _call(model, messages, max_tokens):
    global _current_key_index
    for attempt in range(len(_clients)):
        try:
            client = _clients[_current_key_index % len(_clients)]
            r = await client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
            return r
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                logger.warning("Key %d rate limited, rotating...", _current_key_index % len(_clients))
                _current_key_index += 1
                await asyncio.sleep(1)
            else:
                raise
    raise Exception("All Groq API keys exhausted")

async def translate_question_and_options(question: str, options: list) -> tuple:
    try:
        prompt = (f"Detect if any text contains Hindi or non-English. If so, translate to English. "
                  f"If already English, return as-is.\n"
                  f"Return ONLY JSON: {{\"question\": \"...\", \"options\": [...]}}\n\n"
                  f"Question: {question}\nOptions: {json.dumps(options)}")
        r = await _call("qwen/qwen3-27b", [{"role":"user","content":prompt}], 500)
        data = _parse_json(r.choices[0].message.content)
        return data.get("question", question), data.get("options", options)
    except Exception:
        logger.error("Translation failed", exc_info=True)
        return question, options

async def process_question_image(image_bytes: bytes, options_from_poll: list = None) -> dict:
    """OCR + correct answer in ONE call. Returns {question, options, correct_option, explanation, confidence}"""
    b64 = base64.b64encode(image_bytes).decode()
    hint = f"\nPoll options: {json.dumps(options_from_poll)}\nUse these exact options." if options_from_poll else ""
    prompt = (
        f"You are an SSC/UPSC exam expert. Look at this exam question image.{hint}\n"
        f"1. Extract the question text (translate to English if Hindi).\n"
        f"2. Extract the 4 answer options (remove any A/B/C/D or 1/2/3/4 prefix labels).\n"
        f"3. Determine the correct answer using your knowledge.\n"
        f"4. Write explanation in 6-10 words.\n\n"
        f"Return ONLY JSON:\n"
        f'{{\"question\": \"...\", \"options\": [\"opt1\",\"opt2\",\"opt3\",\"opt4\"], '
        f'\"correct_option\": 0, \"explanation\": \"6-10 word explanation\", \"confidence\": 0.0}}\n'
        f"correct_option is 0-indexed. confidence is 0.0 to 1.0."
    )
    r = await _call("meta-llama/llama-4-scout-17b-16e-instruct",
                    [{"role":"user","content":[
                        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
                        {"type":"text","text":prompt}]}], 700)
    result = _parse_json(r.choices[0].message.content)
    logger.info("OCR+AI: Q='%s' correct=%s conf=%.2f",
                result.get("question","")[:60], result.get("correct_option","?"), result.get("confidence",0))
    return result

async def get_answer_groq_only(question: str, options: list) -> dict:
    opts = "\n".join([f"{chr(65+i)}) {o}" for i, o in enumerate(options)])
    prompt = (f"SSC/UPSC expert. Question: {question}\nOptions:\n{opts}\n"
              f"Return ONLY JSON: {{\"confidence\":0.0,\"correct_option\":0,\"explanation\":\"6-10 word explanation\"}}\n"
              f"correct_option is 0-indexed. Explanation MUST be 6-10 words.")
    r = await _call("qwen/qwen3-27b", [{"role":"user","content":prompt}], 200)
    return _parse_json(r.choices[0].message.content)

async def get_answer_with_context(question: str, options: list, search_results: str) -> dict:
    opts = "\n".join([f"{chr(65+i)}) {o}" for i, o in enumerate(options)])
    prompt = (f"SSC/UPSC expert. Web results:\n{search_results}\n\n"
              f"Question: {question}\nOptions:\n{opts}\n"
              f"Return ONLY JSON: {{\"correct_option\":0,\"explanation\":\"6-10 word explanation\"}}\n"
              f"correct_option is 0-indexed. Explanation MUST be 6-10 words.")
    r = await _call("qwen/qwen3-27b", [{"role":"user","content":prompt}], 200)
    return _parse_json(r.choices[0].message.content)

async def get_explanation_for_answer(question: str, options: list, correct_idx: int) -> str:
    """Generate fresh explanation when admin overrides the answer."""
    correct_text = options[correct_idx] if correct_idx < len(options) else ""
    prompt = (f"SSC/UPSC expert. Question: {question}\n"
              f"The correct answer is: {chr(65+correct_idx)}) {correct_text}\n"
              f"Write a factual explanation in exactly 6-10 words why this is correct.\n"
              f"Return ONLY the explanation text, nothing else.")
    try:
        r = await _call("qwen/qwen3-27b", [{"role":"user","content":prompt}], 100)
        return r.choices[0].message.content.strip()
    except Exception:
        logger.error("Explanation generation failed", exc_info=True)
        return f"{chr(65+correct_idx)}) {correct_text} is the correct answer."
