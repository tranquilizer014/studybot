import logging
from utils.groq_client import (translate_question_and_options, get_answer_groq_only,
                                get_answer_with_context, process_question_image)
from utils.tavily_client import search_question

logger = logging.getLogger(__name__)
CONFIDENCE_THRESHOLD = 0.75

async def get_answer_and_explanation(question: str, options: list) -> dict:
    # Single call translation
    question, options = await translate_question_and_options(question, options)

    try:
        result = await get_answer_groq_only(question, options)
        conf = result.get("confidence", 0)
        logger.info("Groq confidence: %.2f for: %s", conf, question[:60])
        if conf >= CONFIDENCE_THRESHOLD:
            return {"status":"ok","correct_option":result["correct_option"],
                    "explanation":result["explanation"],"source":"groq","options":options,"question":question}
    except Exception:
        logger.error("Groq answer failed", exc_info=True)

    try:
        results = await search_question(question)
        if results:
            result = await get_answer_with_context(question, options, results)
            return {"status":"ok","correct_option":result["correct_option"],
                    "explanation":result["explanation"],"source":"tavily","options":options,"question":question}
    except Exception:
        logger.error("Tavily answer failed", exc_info=True)

    return {"status":"needs_review","correct_option":None,
            "explanation":"AI could not determine answer. Please set manually.",
            "source":"failed","options":options,"question":question}

async def process_image_question(image_bytes: bytes, poll_options: list = None) -> dict:
    try:
        return await process_question_image(image_bytes, poll_options)
    except Exception:
        logger.error("OCR failed", exc_info=True)
        raise
