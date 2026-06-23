import io, logging, re, json
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from database.db import (add_question, mark_question_needs_review,
                          set_admin_state, get_admin_state, clear_admin_state,
                          set_admin_last_text, get_admin_last_text, clear_admin_last_text,
                          add_confirm_poll, get_question_by_id, update_question_text,
                          confirm_question_in_db)
from utils.ai_pipeline import get_answer_and_explanation, process_image_question
from utils.groq_client import get_explanation_for_answer

logger = logging.getLogger(__name__)

def esc(t): return re.sub(r'([_*`\[\]()~>#+=|{}.!-])', r'\\\1', str(t or ""))
def clean_option(text): return re.sub(r'^[\(\[]?[A-Da-d1-4][\)\]\.]\s*', '', text.strip()).strip()
def strip_html(text):
    try: return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()
    except: return re.sub(r'<[^>]+>', '', text).strip()

_pending_photo = {}  # user_id -> {"file_id": ..., "caption": ...}

def _store_pending_photo(user_id, file_id, caption=""):
    _pending_photo[user_id] = {"file_id": file_id, "caption": caption}

def _pop_pending_photo(user_id):
    return _pending_photo.pop(user_id, None)

# ── HTML parsing ──
def parse_html_questions(html_content):
    subject_match = re.search(r'var FILENAME="([^"]+)"', html_content)
    subject = subject_match.group(1) if subject_match else "Quiz"
    total_match = re.search(r'TOTAL_QS=(\d+)', html_content)
    total = int(total_match.group(1)) if total_match else 0

    qs_start = html_content.find('var QS=[')
    if qs_start == -1: return None, subject, 0
    qs_end = html_content.find('];', qs_start)
    if qs_end == -1: return None, subject, 0
    raw = html_content[qs_start+7:qs_end+1]

    def fix_backtick(m):
        inner = m.group(1).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ').replace('\r', '')
        return '"' + inner + '"'
    raw = re.sub(r'`([^`]*)`', fix_backtick, raw)
    raw = re.sub(r'(?<!["\.\w])(\b(?:text|options|correct|explanation)\b)\s*:', r'"\1":', raw)

    try:
        questions_raw = json.loads(raw)
    except Exception as e:
        logger.error("HTML parse failed: %s | sample: %s", e, raw[:300])
        return None, subject, 0

    questions = []
    letter_to_idx = {"A": 0, "B": 1, "C": 2, "D": 3}
    for q in questions_raw:
        text = strip_html(q.get("text", "")).strip()
        options = [strip_html(o).strip() for o in q.get("options", [])]
        correct_letter = q.get("correct", "A").upper()
        correct_idx = letter_to_idx.get(correct_letter, 0)
        explanation = strip_html(q.get("explanation", "")).strip()
        if text and options:
            questions.append({"text": text, "options": options,
                               "correct_index": correct_idx, "explanation": explanation})

    logger.info("[HTML PARSE] subject='%s' found=%d", subject, len(questions))
    return questions, subject, total

async def handle_html_upload(msg, user_id, context):
    doc = msg.document
    if not doc or not doc.file_name.endswith(".html"): return False
    await msg.reply_text("📄 Reading HTML file...")
    try:
        file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        html_content = buf.getvalue().decode("utf-8", errors="ignore")
    except Exception:
        logger.error("HTML download failed", exc_info=True)
        await msg.reply_text("❌ Could not read file.")
        return True

    questions, subject, total = parse_html_questions(html_content)
    if not questions:
        await msg.reply_text("❌ No questions found in this file.")
        return True

    logger.info("[HTML IMPORT] '%s' %d questions", subject, len(questions))
    await set_admin_state(user_id, "awaiting_html_batch_number",
                          {"questions": questions, "subject": subject, "total": len(questions)})
    await msg.reply_text(
        f"📄 *{esc(subject)}*\n\nFound *{len(questions)}* questions — all pre-marked.\n\nEnter a batch number:",
        parse_mode="Markdown")
    return True

async def process_html_batch(msg, user_id, context, state_data):
    text = msg.text.strip() if msg.text else ""
    if not text.isdigit():
        await msg.reply_text("Send a number. Or /cancel.")
        return True
    questions = state_data.get("questions", [])
    subject = state_data.get("subject", "Quiz")
    if not questions:
        await msg.reply_text("❌ No questions found.")
        return True

    added = 0
    for q in questions:
        q_id = await add_question(user_id, q["text"], q["options"],
                                   q["correct_index"], q["explanation"] or "",
                                   photo_file_id=None, ai_source="html_import")
        added += 1
        logger.info("[QUESTION SAVED] QID=%d source=html_import", q_id)

    await clear_admin_state(user_id)
    kb = [[InlineKeyboardButton("📤 Forward All to Quiz Group", callback_data="forward_all_ask")]]
    await msg.reply_text(
        f"✅ *{added} questions added* from *{esc(subject)}*\n\nAll confirmed — ready!\n\nTap below or /admin → Forward All:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return True

def _make_confirm_kb(q_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"qconfirm_{q_id}"),
        InlineKeyboardButton("✏️ Edit Question", callback_data=f"qedit_{q_id}"),
        InlineKeyboardButton("🔄 Override Answer", callback_data=f"qoverride_{q_id}"),
    ]])

async def _save_and_confirm(msg, user_id, context, question_text, options,
                             correct_idx, explanation, photo_file_id, source):
    """Save question and send confirmation poll + action buttons."""
    q_id = await add_question(user_id, question_text, options, correct_idx,
                               explanation, photo_file_id, source)
    logger.info("[QUESTION SAVED] QID=%d", q_id)

    display_opts = [f"{'✅ ' if i==correct_idx else ''}{chr(65+i)}) {o}"[:100]
                    for i, o in enumerate(options)]
    source_tag = "🌐 Web" if source == "tavily" else ("📷 OCR" if source == "ocr" else "🧠 AI")

    sent = await context.bot.send_poll(
        chat_id=msg.chat_id,
        question=f"[{source_tag}] Confirm answer:\n{question_text[:200]}",
        options=display_opts, is_anonymous=False, type="regular", allows_multiple_answers=False)
    await add_confirm_poll(sent.poll.id, q_id)
    await msg.reply_text(
        f"📖 *Explanation:* {esc(explanation)}\n\n"
        f"Tap ✅ in poll to confirm, or use buttons below:",
        parse_mode="Markdown",
        reply_markup=_make_confirm_kb(q_id))

# ── Text question parser ──
def parse_text_question(text: str):
    option_pattern = re.compile(r'^\s*[\(\[]?([A-Da-d1-4])[\)\]\.]\s*(.+)', re.MULTILINE)
    matches = option_pattern.findall(text)
    if len(matches) < 2: return None, None
    first_match = option_pattern.search(text)
    question_text = text[:first_match.start()].strip()
    if not question_text: return None, None
    options = [clean_option(m[1].strip()) for m in matches]
    return question_text, options

async def handle_question_forward(msg, user_id, context) -> bool:
    logger.info("[QUESTION RECEIVED] poll=%s photo=%s text=%s doc=%s",
                bool(msg.poll), bool(msg.photo), bool(msg.text), bool(msg.document))

    if msg.document:
        return await handle_html_upload(msg, user_id, context)

    # ── POLL ──
    if msg.poll:
        poll = msg.poll
        raw_q = poll.question.strip()
        options = [clean_option(o.text) for o in poll.options]
        pending = _pop_pending_photo(user_id)
        last = await get_admin_last_text(user_id)
        if len(raw_q) < 5 and last:
            question_text = last
            await clear_admin_last_text(user_id)
        else:
            question_text = raw_q
        photo_file_id = pending["file_id"] if pending else None

        # Quiz poll — copy everything as-is including existing explanation
        if poll.type == "quiz" and poll.correct_option_id is not None:
            existing_explanation = getattr(poll, 'explanation', '') or ''
            q_id = await add_question(user_id, question_text, options,
                                       poll.correct_option_id,
                                       existing_explanation, photo_file_id, "telegram_quiz")
            logger.info("[QUESTION SAVED] QID=%d [AUTO-CONFIRMED] ANSWER=%s explanation='%s'",
                        q_id, chr(65+poll.correct_option_id), existing_explanation[:40])
            await msg.reply_text(
                f"✅ *Auto-confirmed* — Answer: *{chr(65+poll.correct_option_id)}*\n"
                f"Q: {esc(question_text[:100])}\n"
                f"{'📖 Explanation copied from poll.' if existing_explanation else '_(No explanation in original poll)_'}",
                parse_mode="Markdown",
                reply_markup=_make_confirm_kb(q_id))
            return True

        if not question_text or len(question_text.strip()) < 3:
            await msg.reply_text("❌ No question text. Send text first then poll.")
            return True
        if not options or len(options) < 2:
            await msg.reply_text("❌ Need at least 2 options.")
            return True

        await msg.reply_text("🤖 AI analyzing...")
        ai = await get_answer_and_explanation(question_text, options)
        question_text = ai.get("question", question_text)
        options = [clean_option(o) for o in ai.get("options", options)]

        if ai["status"] == "needs_review":
            q_id = await add_question(user_id, question_text, options, None,
                                       ai["explanation"], photo_file_id, "failed")
            await mark_question_needs_review(q_id, ai["explanation"])
            logger.info("[QUESTION SAVED] QID=%d status=needs_review", q_id)
            sent = await context.bot.send_poll(
                chat_id=msg.chat_id,
                question=f"⚠️ AI failed — tap CORRECT answer:\n{question_text[:200]}",
                options=options, is_anonymous=False, type="regular", allows_multiple_answers=False)
            await add_confirm_poll(sent.poll.id, q_id)
            await msg.reply_text("Tap correct option above.", reply_markup=_make_confirm_kb(q_id))
            return True

        await _save_and_confirm(msg, user_id, context, question_text, options,
                                 ai["correct_option"], ai["explanation"], photo_file_id, ai["source"])
        return True

    # ── PHOTO ──
    elif msg.photo:
        photo_file_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        file = await context.bot.get_file(photo_file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        await msg.reply_text("🔍 Reading image and analyzing...")
        try:
            extracted = await process_image_question(buf.getvalue())
        except Exception:
            logger.error("OCR+AI failed", exc_info=True)
            await msg.reply_text("❌ Could not read image. Try clearer photo.")
            return True

        question_text = extracted.get("question", caption).strip()
        options = [clean_option(o) for o in extracted.get("options", [])]
        correct_idx = extracted.get("correct_option", None)
        explanation = extracted.get("explanation", "")
        confidence = extracted.get("confidence", 0)

        if not question_text or len(question_text.strip()) < 3:
            await msg.reply_text("❌ Could not read question. Try clearer image.")
            return True
        if not options or len(options) < 2:
            _store_pending_photo(user_id, photo_file_id, question_text)
            await set_admin_last_text(user_id, question_text)
            await msg.reply_text(
                f"📝 Question: _{esc(question_text[:100])}_\n\n❌ No options found. Forward poll now.",
                parse_mode="Markdown")
            return True

        if correct_idx is None or confidence < 0.6:
            q_id = await add_question(user_id, question_text, options, None,
                                       explanation, photo_file_id=None, ai_source="ocr_low_conf")
            await mark_question_needs_review(q_id, f"Low confidence ({confidence:.0%})")
            logger.info("[QUESTION SAVED] QID=%d needs_review conf=%.2f", q_id, confidence)
            sent = await context.bot.send_poll(
                chat_id=msg.chat_id,
                question=f"⚠️ Low confidence ({confidence:.0%}) — tap CORRECT:\n{question_text[:200]}",
                options=options, is_anonymous=False, type="regular", allows_multiple_answers=False)
            await add_confirm_poll(sent.poll.id, q_id)
            await msg.reply_text("Tap correct option above.", reply_markup=_make_confirm_kb(q_id))
            return True

        await _save_and_confirm(msg, user_id, context, question_text, options,
                                 correct_idx, explanation, photo_file_id=None, source="ocr")
        return True

    # ── TEXT ──
    elif msg.text and not msg.text.startswith("/"):
        raw = msg.text.strip()
        question_text, options = parse_text_question(raw)

        if question_text and options and len(options) >= 2:
            await msg.reply_text("🤖 AI analyzing...")
            ai = await get_answer_and_explanation(question_text, options)
            question_text = ai.get("question", question_text)
            options = [clean_option(o) for o in ai.get("options", options)]

            if ai["status"] == "needs_review":
                q_id = await add_question(user_id, question_text, options, None,
                                           ai["explanation"], None, "failed")
                await mark_question_needs_review(q_id, ai["explanation"])
                logger.info("[QUESTION SAVED] QID=%d needs_review (text)", q_id)
                sent = await context.bot.send_poll(
                    chat_id=msg.chat_id,
                    question=f"⚠️ AI failed — tap CORRECT:\n{question_text[:200]}",
                    options=options, is_anonymous=False, type="regular", allows_multiple_answers=False)
                await add_confirm_poll(sent.poll.id, q_id)
                await msg.reply_text("Tap correct option above.", reply_markup=_make_confirm_kb(q_id))
            else:
                await _save_and_confirm(msg, user_id, context, question_text, options,
                                         ai["correct_option"], ai["explanation"],
                                         photo_file_id=None, source=ai["source"])
        else:
            await set_admin_last_text(user_id, raw)
            await msg.reply_text("📝 Saved. Forward the poll now.")
        return True

    return False
