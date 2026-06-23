import asyncio, logging, re
from database.db import get_all_chats_by_role, track_message, get_tracked_messages, clear_tracked_messages
from config import ROLE_STUDY_GROUP

logger = logging.getLogger(__name__)

async def send_daily_ping(bot):
    chats = await get_all_chats_by_role(ROLE_STUDY_GROUP)
    for chat_id in chats:
        try:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=(
                    "📚 *Study Reminder*\n\n"
                    "Hi members\\!\n"
                    "Please share your today's study progress in this format:\n\n"
                    "Hours studied \\-\n"
                    "Subjects studied \\-\n"
                    "Topics covered \\-"
                ),
                parse_mode="MarkdownV2")
            await track_message("ping", chat_id, msg.message_id)
            logger.info("Ping sent to %d", chat_id)
        except Exception:
            logger.error("Ping failed chat %d", chat_id, exc_info=True)

async def delete_tracked_messages(bot):
    msgs = await get_tracked_messages()
    logger.info("Deleting %d tracked messages", len(msgs))
    for chat_id, message_id in msgs:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            logger.warning("Could not delete msg %d in %d", message_id, chat_id)
    await clear_tracked_messages()
