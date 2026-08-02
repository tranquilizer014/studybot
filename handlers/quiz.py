import json, asyncio, logging, re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.db import (create_batch, set_batch_results_message, get_batch,
    get_batch_scores, add_poll_map, get_poll_map, upsert_score, mark_questions_sent,
    confirm_question_in_db, get_confirm_poll, delete_confirm_poll,
    record_submit, get_user_batch_score)
from config import DB_PATH, ADMIN_USER_ID

logger = logging.getLogger(__name__)
def esc(t): return re.sub(r'([_*`\[\]()~>#+=|{}.!-])', r'\\\1', str(t or ""))

def _fmt_time(first, submitted):
    if not first or not submitted: return ""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        t1 = datetime.strptime(first[:19], fmt)
        t2 = datetime.strptime(submitted[:19], fmt)
        secs = int((t2 - t1).total_seconds())
        if secs <= 0: return ""
        if secs < 60: return f"⏱ {secs}s"
        return f"⏱ {secs//60}m {secs%60}s"
    except Exception: return ""

async def send_batch_to_channel(context, chat_id, questions, batch_number, subject=None):
    q_ids = [q[0] for q in questions]
    batch_id = await create_batch(batch_number, chat_id, q_ids)
    total = len(questions)
    logger.info("[BATCH SEND] batch#%d %d questions → chat %d", batch_number, total, chat_id)

    # Send subject header before first poll
    header = subject or f"Batch #{batch_number}"
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📚 *{esc(header)}*\n_{total} Questions_",
            parse_mode="MarkdownV2")
        await asyncio.sleep(3.1)
    except Exception:
        logger.error("Failed to send batch header", exc_info=True)

    for display_num, q in enumerate(questions, start=1):
        q_id, _, q_text, opts_json, correct_opt, explanation, _ = q[0],q[1],q[2],q[3],q[4],q[5],q[6]
        options = json.loads(opts_json)
        # Format: "Q. 1/40  Question text here"
        poll_question = f"Q. {display_num}/{total}  {q_text}"[:300]

        for attempt in range(3):
            try:
                sent = await context.bot.send_poll(
                    chat_id=chat_id,
                    question=poll_question,
                    options=options,
                    type="quiz",
                    correct_option_id=correct_opt,
                    explanation=(explanation[:200] if explanation else None),
                    explanation_parse_mode="Markdown",
                    is_anonymous=False,
                )
                await add_poll_map(sent.poll.id, batch_id, q_id, correct_opt, explanation, display_num-1)
                logger.info("[QUESTION SENT] QID=%d Q%d/%d batch#%d", q_id, display_num, total, batch_number)
                break
            except Exception as e:
                if hasattr(e, 'retry_after'):
                    wait = e.retry_after + 1
                    logger.warning("[FLOOD CONTROL] waiting %ds", wait)
                    await asyncio.sleep(wait)
                else:
                    logger.error("[QUESTION FAILED] QID=%d attempt=%d", q_id, attempt+1, exc_info=True)
                    if attempt == 2: break
                    await asyncio.sleep(5)
        await asyncio.sleep(3.1)

    # Submit button after last question
    kb = [[InlineKeyboardButton("✅ Submit Batch", callback_data=f"submit_batch_{batch_id}")]]
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📝 *Batch \\#{batch_number} — Done\\!*\nTap Submit to record your time\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        logger.error("Failed to send submit button", exc_info=True)
    await asyncio.sleep(3.1)

    # Leaderboard card
    scores = await get_batch_scores(batch_id)
    card = _batch_card(batch_number, scores)
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=card, parse_mode="MarkdownV2")
        await set_batch_results_message(batch_id, msg.message_id)
    except Exception:
        logger.error("Batch card failed", exc_info=True)
    await mark_questions_sent(q_ids)

def _batch_card(batch_number, scores):
    lines = [f"📊 *Batch \\#{batch_number} — Leaderboard*\n"]
    if not scores:
        lines.append("_Waiting for answers\\.\\.\\._")
        return "\n".join(lines)
    medals = ["🥇","🥈","🥉"]
    for i, row in enumerate(scores):
        username = row[0]; score = row[1]; tot = row[2]
        first_at = row[3] if len(row) > 3 else None
        submitted_at = row[4] if len(row) > 4 else None
        pct = round(score/tot*100) if tot else 0
        time_str = _fmt_time(first_at, submitted_at) if submitted_at else ""
        medal = medals[i] if i < 3 else "👤"
        line = f"{medal} {esc(username)} — {score}/{tot} \\({pct}%\\)"
        if time_str:
            line += f" {esc(time_str)}"
        lines.append(line)
    return "\n".join(lines)

async def handle_submit_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    batch_id = int(query.data.split("_")[2])

    await record_submit(batch_id, user.id)

    row = await get_user_batch_score(batch_id, user.id)
    if not row:
        await query.answer("No answers recorded yet.", show_alert=True)
        return

    username, score, total, first_at, submitted_at = row
    pct = round(score/total*100) if total else 0
    time_str = _fmt_time(first_at, submitted_at)

    result_lines = [
        f"📊 *Your Batch Result*\n",
        f"Score: *{score}/{total}* \\({pct}%\\)",
    ]
    if time_str:
        result_lines.append(f"Time: *{esc(time_str)}*")

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text="\n".join(result_lines),
            parse_mode="MarkdownV2")
    except Exception:
        logger.warning("Could not DM result to user %d", user.id)

    # Update leaderboard card
    batch = await get_batch(batch_id)
    if batch:
        scores = await get_batch_scores(batch_id)
        card = _batch_card(batch[1], scores)
        try:
            await context.bot.edit_message_text(
                chat_id=batch[2], message_id=batch[3],
                text=card, parse_mode="MarkdownV2")
        except Exception:
            logger.error("Batch card update failed", exc_info=True)

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user = answer.user
    chosen = answer.option_ids[0] if answer.option_ids else None
    if chosen is None: return

    # Admin confirmation poll
    if user.id == ADMIN_USER_ID:
        q_id = await get_confirm_poll(poll_id)
        if q_id is not None:
            logger.info("[CONFIRM POLL RECEIVED] POLL_ID=%s QID=%d ANSWER=%s",
                        poll_id, q_id, chr(65+chosen))
            import aiosqlite
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT explanation FROM question_queue WHERE id=?", (q_id,)) as c:
                    row = await c.fetchone()
                    explanation = row[0] if row else ""
            await confirm_question_in_db(q_id, chosen, explanation)
            await delete_confirm_poll(poll_id)
            try:
                await context.bot.send_message(user.id,
                    f"✅ Q{q_id} confirmed — Answer: *{chr(65+chosen)}*", parse_mode="Markdown")
            except Exception: pass
            return

    # Student answer
    poll_data = await get_poll_map(poll_id)
    if not poll_data: return
    _, batch_id, question_id, correct_option, explanation, question_index = poll_data
    is_correct = (chosen == correct_option)
    username = user.username or user.first_name
    batch = await get_batch(batch_id)
    if not batch: return
    batch_number = batch[1]
    total = len(json.loads(batch[4]))
    await upsert_score(batch_id, user.id, username, is_correct, total)
    logger.info("User %s Q%d batch#%d: %s", username, question_index+1, batch_number,
                "✅" if is_correct else "❌")

    scores = await get_batch_scores(batch_id)
    card = _batch_card(batch_number, scores)
    try:
        await context.bot.edit_message_text(
            chat_id=batch[2], message_id=batch[3],
            text=card, parse_mode="MarkdownV2")
    except Exception:
        logger.error("Batch card update failed", exc_info=True)
