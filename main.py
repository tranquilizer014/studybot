import asyncio, logging
from aiohttp import web
from telegram import Update
from telegram.ext import (Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, PollAnswerHandler, ChatMemberHandler, filters)
from config import TELEGRAM_TOKEN, ADMIN_USER_ID, HEALTH_PORT, DB_PATH, GROQ_API_KEYS
from database.db import (init_db, backup_db, get_due_scheduled_messages,
    mark_scheduled_sent, register_known_chat)
from handlers.admin import (admin_menu, admin_callback, handle_admin_forward,
    cancel_command, setup_chat, _send_broadcast)
from handlers.quiz import handle_poll_answer, handle_submit_batch
from handlers.tracker import send_daily_ping, delete_tracked_messages

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)
_bot_ready = False

async def health_handler(request):
    if not _bot_ready: return web.Response(status=503, text="Starting...")
    try:
        import aiosqlite
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("SELECT 1")
        return web.Response(status=200, text="OK")
    except Exception as e: return web.Response(status=500, text=str(e))

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HEALTH_PORT).start()
    logger.info("Health server on port %d", HEALTH_PORT)

async def dispatch_scheduled(bot):
    try:
        from handlers.quiz import send_batch_to_channel
        from database.db import get_queue_questions
        import json as _j
        for row in await get_due_scheduled_messages():
            msg_id, admin_id, chat_id, msg_type, text, file_id, caption, *_ = row
            if msg_type == "batch":
                data = _j.loads(text or "{}")
                bn = data.get("batch_number")
                q_ids = data.get("q_ids", [])
                if not bn or not q_ids:
                    logger.error("Scheduled batch missing batch_number or q_ids: %s", data)
                    await mark_scheduled_sent(msg_id)
                    continue
                bn = int(bn)
                all_qs = await get_queue_questions()
                id_set = set(int(x) for x in q_ids)
                confirmed = [q for q in all_qs if q[0] in id_set and q[7] == "confirmed"]
                if confirmed:
                    class Ctx: pass
                    ctx = Ctx(); ctx.bot = bot
                    await send_batch_to_channel(ctx, chat_id, confirmed, bn)
                    logger.info("Dispatched scheduled batch #%d to %d", bn, chat_id)
            else:
                class Ctx: pass
                ctx = Ctx(); ctx.bot = bot
                await _send_broadcast(ctx, chat_id,
                    msg_data={"type":msg_type,"text":text,"file_id":file_id,"caption":caption or ""})
            await mark_scheduled_sent(msg_id)
    except Exception: logger.error("Scheduled dispatch failed", exc_info=True)

async def start_command(update: Update, context):
    uid = update.effective_user.id
    if uid == ADMIN_USER_ID:
        await update.message.reply_text(
            "👋 *Welcome Admin!*\n\n/admin — Panel\n/cancel — Cancel\n\n"
            "Forward questions here to add to queue.", parse_mode="Markdown")
    else:
        await update.message.reply_text("👋 *Study Bot*\n\nAnswer quiz polls in the group!",
                                        parse_mode="Markdown")

async def handle_private(update: Update, context):
    if not update.message: return
    await handle_admin_forward(update, context)

async def handle_callback(update: Update, context):
    q = update.callback_query
    if not q: return
    if q.data.startswith("submit_batch_"):
        await handle_submit_batch(update, context)
    else:
        await admin_callback(update, context)

async def handle_chat_member(update: Update, context):
    try:
        result = update.my_chat_member
        if result and result.new_chat_member.status in ["member","administrator"]:
            chat = result.chat
            chat_type = "channel" if chat.type == "channel" else "group"
            await register_known_chat(chat.id, chat.title or str(chat.id), chat_type)
            logger.info("Bot added to %s %d: %s", chat_type, chat.id, chat.title)
    except Exception: logger.error("Chat member tracking failed", exc_info=True)

async def post_init(application):
    global _bot_ready
    await start_health_server()
    await init_db()

    if not GROQ_API_KEYS:
        raise ValueError("No Groq API keys. Set GROQ_API_KEY or GROQ_API_KEY_1 etc.")
    logger.info("Loaded %d Groq key(s)", len(GROQ_API_KEYS))

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from config import (DAILY_PING_HOUR, DAILY_PING_MINUTE, CLEANUP_HOUR, CLEANUP_MINUTE)

    bot = application.bot
    s = AsyncIOScheduler()

    async def ping(): await send_daily_ping(bot)
    async def cleanup(): await delete_tracked_messages(bot)
    async def dispatch(): await dispatch_scheduled(bot)

    # 9:00 PM IST = 15:30 UTC
    s.add_job(ping, CronTrigger(hour=DAILY_PING_HOUR, minute=DAILY_PING_MINUTE, timezone="UTC"),
              id="ping", replace_existing=True)
    # 11:59 PM IST = 18:29 UTC
    s.add_job(cleanup, CronTrigger(hour=CLEANUP_HOUR, minute=CLEANUP_MINUTE, timezone="UTC"),
              id="cleanup", replace_existing=True)
    # Every minute — scheduled messages
    s.add_job(dispatch, CronTrigger(minute="*", timezone="UTC"), id="dispatch", replace_existing=True)
    # 3 AM IST = 21:30 UTC
    s.add_job(backup_db, CronTrigger(hour=21, minute=30, timezone="UTC"), id="backup", replace_existing=True)
    s.start()
    logger.info("Scheduler started. 9PM IST=15:30UTC 11:59PM=18:29UTC")

    _bot_ready = True
    logger.info("Bot ready — %d Groq key(s)", len(GROQ_API_KEYS))

async def error_handler(update, context):
    logger.error("Unhandled exception: %s", context.error, exc_info=context.error)

def main():
    if not TELEGRAM_TOKEN: raise ValueError("TELEGRAM_TOKEN not set")
    if ADMIN_USER_ID == 0: raise ValueError("ADMIN_USER_ID not set")
    logger.info("Starting StudyBot...")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("setup", setup_chat))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, handle_private))

    logger.info("Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
