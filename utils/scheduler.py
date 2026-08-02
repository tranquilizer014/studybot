from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from config import (
    DAILY_PING_HOUR, DAILY_PING_MINUTE,
    SUMMARY_HOUR, SUMMARY_MINUTE,
    CHECKIN_REMINDER_HOUR, CHECKIN_REMINDER_MINUTE,
    WEEKLY_SUMMARY_DAY
)

def setup_scheduler(application):
    from handlers.tracker import (
        send_daily_ping, send_daily_summary,
        send_checkin_reminder, send_weekly_summary
    )

    scheduler = AsyncIOScheduler()

    # 9:00 PM - Daily ping
    scheduler.add_job(
        lambda: application.create_task(send_daily_ping(application.bot._context)),
        CronTrigger(hour=DAILY_PING_HOUR, minute=DAILY_PING_MINUTE),
        id="daily_ping"
    )

    # 9:45 PM - Reminder
    scheduler.add_job(
        lambda: application.create_task(send_checkin_reminder(application.bot._context)),
        CronTrigger(hour=CHECKIN_REMINDER_HOUR, minute=CHECKIN_REMINDER_MINUTE),
        id="checkin_reminder"
    )

    # 10:00 PM - Summary
    scheduler.add_job(
        lambda: application.create_task(send_daily_summary(application.bot._context)),
        CronTrigger(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE),
        id="daily_summary"
    )

    # Sunday 9 PM - Weekly summary
    scheduler.add_job(
        lambda: application.create_task(send_weekly_summary(application.bot._context)),
        CronTrigger(day_of_week=WEEKLY_SUMMARY_DAY, hour=DAILY_PING_HOUR, minute=DAILY_PING_MINUTE),
        id="weekly_summary"
    )

    scheduler.start()
    return scheduler
