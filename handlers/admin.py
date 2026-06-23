import json, logging, re, asyncio, io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ADMIN_USER_ID, ROLE_QUIZ_GROUP, ROLE_STUDY_GROUP
from database.db import (
    set_chat_role, set_chat_active, get_all_chats_by_role, get_all_role_chats,
    remove_chat_role, register_known_chat, get_known_chats, remove_known_chat,
    get_queue_questions, clear_queue, confirm_question_in_db, get_question_by_id,
    set_admin_state, get_admin_state, clear_admin_state,
    set_admin_last_text, get_admin_last_text, clear_admin_last_text,
    add_scheduled_message, get_pending_scheduled_messages, get_last_batch_number,
    delete_scheduled_message, update_question_text,
)
from handlers.admin_question import handle_question_forward

logger = logging.getLogger(__name__)
def is_admin(uid): return uid == ADMIN_USER_ID
def esc(t): return re.sub(r'([_*`\[\]()~>#+=|{}.!-])', r'\\\1', str(t or ""))

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    kb = [
        [InlineKeyboardButton("📋 Preview Queue", callback_data="preview_queue"),
         InlineKeyboardButton("📤 Forward All", callback_data="forward_all_ask")],
        [InlineKeyboardButton("📁 Broadcast", callback_data="broadcast_menu")],
        [InlineKeyboardButton("⚙️ Setup Chats", callback_data="setup_menu"),
         InlineKeyboardButton("⏸ Pause/Resume", callback_data="pause_menu")],
        [InlineKeyboardButton("🗑 Clear Queue", callback_data="clear_queue")],
    ]
    await update.message.reply_text("🛠 *Admin Panel*", parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(kb))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid): return
    d = q.data

    if d == "pause_menu": await _pause_menu(q)
    elif d.startswith("pause_"): await set_chat_active(int(d.split("_")[1]), False); await q.edit_message_text("⏸ Paused.")
    elif d.startswith("resume_"): await set_chat_active(int(d.split("_")[1]), True); await q.edit_message_text("▶ Resumed.")
    elif d == "clear_queue": await clear_queue(); await q.edit_message_text("🗑 Queue cleared.")
    elif d == "preview_queue": await _preview(q)
    elif d == "forward_all_ask": await _ask_batch(q, uid)
    elif d == "forward_confirmed": await _forward(q, context, uid)
    elif d == "view_sched_batches": await _view_sched_batches(q)
    elif d == "schedule_batch_ask":
        await set_admin_state(uid, "awaiting_batch_schedule_time", {})
        await q.edit_message_text("⏰ When to send? `DD/MM HH:MM` IST\ne.g. `06/06 21:00`\n\nOr /cancel.", parse_mode="Markdown")
    elif d.startswith("cancel_batch_"): await _cancel_sched_batch(q, int(d.split("_")[2]))
    elif d.startswith("qconfirm_"): await _qconfirm(q, uid, int(d.split("_")[1]))
    elif d.startswith("qedit_"): await _qedit(q, uid, int(d.split("_")[1]))
    elif d.startswith("qoverride_"): await _qoverride(q, uid, int(d.split("_")[1]))
    elif d == "broadcast_menu": await _broadcast_menu(q)
    elif d.startswith("bcast_"):
        parts = d.split("_", 2); action, chat_id = parts[1], int(parts[2])
        await set_admin_state(uid, "broadcast_now" if action=="now" else "broadcast_schedule_content", {"chat_id": chat_id})
        await q.edit_message_text("📤 Send the message or file.\n\nOr /cancel.")
    elif d == "setup_menu": await _setup_menu(q)
    elif d == "set_quiz_group":
        await set_admin_state(uid, "awaiting_chat_id", {"role": ROLE_QUIZ_GROUP})
        await q.edit_message_text("🎯 Forward a message from Quiz Group or type its ID.\n\nOr /cancel.")
    elif d == "set_study_group":
        await set_admin_state(uid, "awaiting_chat_id", {"role": ROLE_STUDY_GROUP})
        await q.edit_message_text("📚 Forward a message from Study Group or type its ID.\n\nOr /cancel.")
    elif d.startswith("remove_role_"):
        parts = d.split("_"); chat_id = int(parts[2]); role = "_".join(parts[3:])
        await remove_chat_role(chat_id, role)
        await q.edit_message_text(f"✅ Removed.")
    elif d.startswith("remove_known_"):
        await remove_known_chat(int(d.split("_")[2]))
        await q.edit_message_text("✅ Removed from broadcast list.")
    elif d.startswith("cancel_sched_"):
        msg_id = int(d.split("_")[2])
        await delete_scheduled_message(msg_id)
        await q.edit_message_text("✅ Scheduled message cancelled.")
    elif d == "view_scheduled":
        await _view_scheduled(q)

async def _pause_menu(q):
    kb = []
    for role, label in [(ROLE_QUIZ_GROUP,"Quiz Group"),(ROLE_STUDY_GROUP,"Study Group")]:
        for cid in await get_all_chats_by_role(role):
            kb.append([InlineKeyboardButton(f"⏸ {label}", callback_data=f"pause_{cid}"),
                       InlineKeyboardButton(f"▶ {label}", callback_data=f"resume_{cid}")])
    if not kb: await q.edit_message_text("No chats configured."); return
    await q.edit_message_text("⏸ Pause / Resume", reply_markup=InlineKeyboardMarkup(kb))

async def _ask_batch(q, uid):
    last = await get_last_batch_number()
    await set_admin_state(uid, "awaiting_batch_number", {})
    kb = [[InlineKeyboardButton("📅 View Scheduled Batches", callback_data="view_sched_batches")]]
    await q.edit_message_text(
        f"📦 Last batch: *#{last}*\nEnter new batch number:\n\nOr /cancel.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def _preview(q):
    qs = await get_queue_questions()
    if not qs: await q.edit_message_text("📋 Queue is empty."); return
    lines = [f"*📋 Queue — {len(qs)} question(s):*\n"]
    for i, question in enumerate(qs, 1):
        icon = {"confirmed":"✅","pending":"⏳","needs_review":"⚠️"}.get(question[7],"❓")
        lines.append(f"{icon} {i}. {esc(question[2][:70])}")
    kb = [[InlineKeyboardButton("📤 Forward All", callback_data="forward_all_ask"),
           InlineKeyboardButton("🗑 Clear", callback_data="clear_queue")]]
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(kb))

async def _forward(q, context, uid):
    _, data = await get_admin_state(uid)
    batch_number = data.get("batch_number")
    if not batch_number:
        await q.edit_message_text("❌ Batch number lost. Tap Forward All again.")
        return
    await clear_admin_state(uid)
    from handlers.quiz import send_batch_to_channel
    qs = await get_queue_questions()
    confirmed = [x for x in qs if x[7] == "confirmed"]
    if not confirmed:
        pending = sum(1 for x in qs if x[7]=="pending")
        review = sum(1 for x in qs if x[7]=="needs_review")
        msg = "❌ No confirmed questions."
        if pending: msg += f"\n⏳ {pending} awaiting confirmation."
        if review: msg += f"\n⚠️ {review} need manual answer."
        await q.edit_message_text(msg); return
    quiz_chats = await get_all_chats_by_role(ROLE_QUIZ_GROUP)
    if not quiz_chats: await q.edit_message_text("❌ No quiz group set."); return
    await q.edit_message_text(f"📤 Sending {len(confirmed)} questions as Batch #{batch_number}...")
    for cid in quiz_chats:
        await send_batch_to_channel(context, cid, confirmed, batch_number)


async def _view_sched_batches(q):
    pending = await get_pending_scheduled_messages()
    batches = [r for r in pending if r[3] == "batch"]
    if not batches:
        await q.edit_message_text("📅 No scheduled batches.")
        return
    lines = ["📅 *Scheduled Batches*\n"]
    kb = []
    import json as _j
    for row in batches:
        msg_id, _, chat_id, _, text_json, _, _, sched_time = row[0],row[1],row[2],row[3],row[4],row[5],row[6],row[7]
        data = _j.loads(text_json or "{}")
        bn = data.get("batch_number", "?")
        lines.append(f"• Batch #{bn} — {sched_time} UTC")
        kb.append([InlineKeyboardButton(f"❌ Cancel Batch #{bn}", callback_data=f"cancel_batch_{msg_id}")])
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(kb))

async def _cancel_sched_batch(q, msg_id):
    await delete_scheduled_message(msg_id)
    await q.edit_message_text("✅ Scheduled batch cancelled.")


async def _qconfirm(q, uid, q_id):
    qobj = await get_question_by_id(q_id)
    if not qobj: await q.edit_message_text("❌ Not found."); return
    if qobj[7] == "needs_review" or qobj[4] is None:
        await q.edit_message_text("⚠️ Use Override to set answer manually."); return
    await confirm_question_in_db(q_id, qobj[4], qobj[5])
    await q.edit_message_text(f"✅ Confirmed — Answer: *{chr(65+qobj[4])}*", parse_mode="Markdown")

async def _qedit(q, uid, q_id):
    await set_admin_state(uid, "awaiting_question_edit", {"q_id": q_id})
    qobj = await get_question_by_id(q_id)
    current = qobj[2] if qobj else ""
    await q.edit_message_text(
        f"✏️ *Edit Question*\n\nCurrent:\n_{esc(current[:200])}_\n\nSend the corrected question text.\nOr /cancel.",
        parse_mode="Markdown")

async def _qoverride(q, uid, q_id):
    qobj = await get_question_by_id(q_id)
    await set_admin_state(uid, "awaiting_qanswer_override", {"q_id": q_id, "explanation": qobj[5] if qobj else ""})
    await q.edit_message_text("✏️ Type correct letter: *A, B, C or D*\n\nOr /cancel.", parse_mode="Markdown")

async def _broadcast_menu(q):
    chats = await get_known_chats()
    if not chats: await q.edit_message_text("❌ No chats. Add bot to chats first."); return
    kb = []
    for chat_id, title, chat_type in chats:
        icon = "📺" if chat_type == "channel" else "📚"
        safe = (title or str(chat_id))[:20]
        kb.append([InlineKeyboardButton(f"{icon} {safe}", callback_data=f"bcast_now_{chat_id}"),
                   InlineKeyboardButton("⏰", callback_data=f"bcast_sched_{chat_id}"),
                   InlineKeyboardButton("❌", callback_data=f"remove_known_{chat_id}")])
    pending = await get_pending_scheduled_messages()
    if pending:
        kb.append([InlineKeyboardButton(f"📅 View {len(pending)} Scheduled", callback_data="view_scheduled")])
    await q.edit_message_text("📁 *Broadcast*", parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(kb))

async def _view_scheduled(q):
    pending = await get_pending_scheduled_messages()
    if not pending:
        await q.edit_message_text("📅 No scheduled messages.")
        return
    lines = ["📅 *Scheduled Messages*\n"]
    kb = []
    for row in pending:
        msg_id, admin_id, chat_id, msg_type, text, file_id, caption, sched_time, *_ = row
        preview = (text or caption or f"[{msg_type}]")[:40]
        lines.append(f"• `{sched_time} UTC` → `{chat_id}`\n  _{preview}_")
        kb.append([InlineKeyboardButton(f"❌ Cancel: {sched_time}", callback_data=f"cancel_sched_{msg_id}")])
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(kb))

async def _setup_menu(q):
    role_chats = await get_all_role_chats()
    lines = ["⚙️ *Chat Setup*\n"]
    kb = []
    for chat_id, role, title, _ in role_chats:
        label = "🎯 Quiz Group" if role == ROLE_QUIZ_GROUP else "📚 Study Group"
        lines.append(f"{label}: `{title or chat_id}`")
        kb.append([InlineKeyboardButton(f"❌ Remove {label}", callback_data=f"remove_role_{chat_id}_{role}")])
    if not role_chats: lines.append("_No chats configured._")
    kb += [[InlineKeyboardButton("🎯 Set Quiz Group", callback_data="set_quiz_group")],
           [InlineKeyboardButton("📚 Set Study Group", callback_data="set_study_group")]]
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(kb))

async def setup_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    ctype = update.effective_chat.type; cid = update.effective_chat.id
    if ctype in ["group","supergroup"]:
        kb = [[InlineKeyboardButton("🎯 Quiz Group", callback_data=f"setrole_quiz_{cid}"),
               InlineKeyboardButton("📚 Study Group", callback_data=f"setrole_study_{cid}")]]
        await update.message.reply_text("What is this chat?", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("Use /admin → Setup Chats.")

def _extract(msg):
    if msg.text: return {"type":"text","text":msg.text}
    if msg.photo: return {"type":"photo","file_id":msg.photo[-1].file_id,"caption":msg.caption or ""}
    if msg.document: return {"type":"document","file_id":msg.document.file_id,"caption":msg.caption or ""}
    if msg.video: return {"type":"video","file_id":msg.video.file_id,"caption":msg.caption or ""}
    return {"type":"text","text":""}

async def _send_broadcast(context, chat_id, msg=None, msg_data=None):
    if msg_data is None and msg: msg_data = _extract(msg)
    t = msg_data.get("type","text")
    try:
        if t == "text":
            txt = msg_data.get("text") or ""
            if txt: await context.bot.send_message(chat_id=chat_id, text=txt)
        elif t == "photo": await context.bot.send_photo(chat_id=chat_id, photo=msg_data["file_id"], caption=msg_data.get("caption",""))
        elif t == "document": await context.bot.send_document(chat_id=chat_id, document=msg_data["file_id"], caption=msg_data.get("caption",""))
        elif t == "video": await context.bot.send_video(chat_id=chat_id, video=msg_data["file_id"], caption=msg_data.get("caption",""))
        logger.info("Broadcast → %d type=%s", chat_id, t)
    except Exception: logger.error("Broadcast failed %d", chat_id, exc_info=True)

async def handle_admin_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return False
    msg = update.message
    if not msg: return False
    state, sd = await get_admin_state(uid)

    if state == "awaiting_batch_number":
        text = msg.text.strip() if msg.text else ""
        if not text.isdigit(): await msg.reply_text("Send a number. Or /cancel."); return True
        await set_admin_state(uid, "batch_number_set", {"batch_number": int(text)})
        kb = [
            [InlineKeyboardButton("📤 Send Now", callback_data="forward_confirmed")],
            [InlineKeyboardButton("⏰ Schedule Batch", callback_data="schedule_batch_ask")],
        ]
        await msg.reply_text(f"📦 Batch *#{text}* ready.", parse_mode="Markdown",
                             reply_markup=InlineKeyboardMarkup(kb))
        return True

    if state == "awaiting_chat_id":
        role = sd.get("role"); chat_id = None; title = ""
        if msg.forward_from_chat:
            chat_id = msg.forward_from_chat.id; title = msg.forward_from_chat.title or str(chat_id)
        elif msg.text and msg.text.strip().lstrip("-").isdigit():
            chat_id = int(msg.text.strip()); title = str(chat_id)
        if chat_id:
            await set_chat_role(chat_id, role, title, "group")
            await register_known_chat(chat_id, title, "group")
            await clear_admin_state(uid)
            label = "Quiz Group 🎯" if role == ROLE_QUIZ_GROUP else "Study Group 📚"
            await msg.reply_text(f"✅ *{label}* set! ID: `{chat_id}`", parse_mode="Markdown")
        else:
            await msg.reply_text("❌ Forward a message from the group or type its ID.")
        return True

    if state == "broadcast_now":
        chat_id = sd.get("chat_id"); await clear_admin_state(uid)
        await _send_broadcast(context, chat_id, msg=msg)
        await msg.reply_text(f"✅ Sent to `{chat_id}`", parse_mode="Markdown")
        return True

    if state == "broadcast_schedule_content":
        await set_admin_state(uid, "broadcast_schedule_time",
                              {"chat_id": sd["chat_id"], "msg_data": _extract(msg)})
        await msg.reply_text("⏰ When? `DD/MM HH:MM` IST e.g. `06/06 21:00`\n\nOr /cancel.",
                             parse_mode="Markdown")
        return True

    if state == "broadcast_schedule_time":
        text = msg.text.strip() if msg.text else ""
        try:
            ist = datetime.strptime(text, "%d/%m %H:%M").replace(year=datetime.now().year)
            utc = ist - timedelta(hours=5, minutes=30)
            utc_str = utc.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            await msg.reply_text("❌ Use `DD/MM HH:MM` e.g. `06/06 21:00`", parse_mode="Markdown")
            return True
        md = sd.get("msg_data", {})
        await add_scheduled_message(uid, sd["chat_id"], md.get("type","text"),
                                    utc_str, md.get("text"), md.get("file_id"), md.get("caption"))
        await clear_admin_state(uid)
        await msg.reply_text(f"✅ Scheduled *{ist.strftime('%d %b %H:%M')} IST* → `{sd['chat_id']}`",
                             parse_mode="Markdown")
        return True

    if state == "awaiting_question_edit":
        new_text = msg.text.strip() if msg.text else ""
        if not new_text: await msg.reply_text("Send question text. Or /cancel."); return True
        q_id = sd.get("q_id")
        await update_question_text(q_id, new_text)
        await clear_admin_state(uid)
        from handlers.admin_question import _make_confirm_kb
        await msg.reply_text(
            f"✅ Question updated!\n\n*New:* {esc(new_text[:200])}",
            parse_mode="Markdown", reply_markup=_make_confirm_kb(q_id))
        return True

    if state == "awaiting_qanswer_override":
        text = msg.text.strip().upper() if msg.text else ""
        if text not in ["A","B","C","D"]: await msg.reply_text("Send A, B, C or D. Or /cancel."); return True
        q_id = sd.get("q_id")
        opt_idx = ord(text) - ord("A")
        qobj = await get_question_by_id(q_id) if q_id else None
        if qobj:
            import json as _j
            options = _j.loads(qobj[3])
            from utils.groq_client import get_explanation_for_answer
            new_explanation = await get_explanation_for_answer(qobj[2], options, opt_idx)
            await confirm_question_in_db(q_id, opt_idx, new_explanation)
            await clear_admin_state(uid)
            await msg.reply_text(
                f"✅ Answer set to *{text}*\n📖 New explanation: {esc(new_explanation)}",
                parse_mode="Markdown")
        else:
            await msg.reply_text("❌ Question not found.")
        return True

    if state == "awaiting_html_batch_number":
        from handlers.admin_question import process_html_batch
        return await process_html_batch(msg, uid, context, sd)

    if state == "awaiting_batch_schedule_time":
        text = msg.text.strip() if msg.text else ""
        try:
            from datetime import timedelta
            ist = datetime.strptime(text, "%d/%m %H:%M").replace(year=datetime.now().year)
            utc = ist - timedelta(hours=5, minutes=30)
            utc_str = utc.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            await msg.reply_text("❌ Use `DD/MM HH:MM` e.g. `06/06 21:00`", parse_mode="Markdown")
            return True
        # Get batch data from state
        _, bdata = await get_admin_state(uid)
        batch_number = bdata.get("batch_number", 1)
        qs = await get_queue_questions()
        confirmed = [x for x in qs if x[7] == "confirmed"]
        quiz_chats = await get_all_chats_by_role(ROLE_QUIZ_GROUP)
        if not confirmed:
            await msg.reply_text("❌ No confirmed questions to schedule.")
            return True
        if not quiz_chats:
            await msg.reply_text("❌ No quiz group set.")
            return True
        q_ids = [q[0] for q in confirmed]
        import json as _j
        from database.db import add_scheduled_message as _add_sched
        batch_json = _j.dumps({"batch_number": int(batch_number), "q_ids": [int(x) for x in q_ids]})
        logger.info("[BATCH SCHEDULED] batch#%d %d questions at %s UTC", batch_number, len(q_ids), utc_str)
        for cid in quiz_chats:
            await _add_sched(uid, cid, "batch", utc_str, batch_json)
        await clear_admin_state(uid)
        await msg.reply_text(
            f"✅ Batch *#{batch_number}* scheduled for *{ist.strftime('%d %b %H:%M')} IST*",
            parse_mode="Markdown")
        return True

    if state == "awaiting_override":
        text = msg.text.strip().upper() if msg.text else ""
        if text not in ["A","B","C","D"]: await msg.reply_text("Send A, B, C or D. Or /cancel."); return True
        await confirm_question_in_db(sd["q_id"], ord(text)-ord("A"), sd.get("explanation",""))
        await clear_admin_state(uid)
        await msg.reply_text(f"✅ Answer set to *{text}*.", parse_mode="Markdown")
        return True

    # Route documents (HTML files) to question handler
    return await handle_question_forward(msg, uid, context)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await clear_admin_state(update.effective_user.id)
    await update.message.reply_text("❌ Cancelled.")
