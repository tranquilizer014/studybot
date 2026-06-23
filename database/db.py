import aiosqlite, json, logging, os, shutil
from datetime import datetime
from config import DB_PATH, BACKUP_DIR

logger = logging.getLogger(__name__)

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_roles (
                chat_id INTEGER, role TEXT NOT NULL, is_active INTEGER DEFAULT 1,
                title TEXT, chat_type TEXT, PRIMARY KEY(chat_id, role)
            );
            CREATE TABLE IF NOT EXISTS known_chats (
                chat_id INTEGER PRIMARY KEY, title TEXT, chat_type TEXT,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS question_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER,
                question_text TEXT, options TEXT, correct_option INTEGER,
                explanation TEXT, photo_file_id TEXT,
                status TEXT DEFAULT 'pending', ai_source TEXT DEFAULT 'unknown',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS admin_confirm_polls (
                poll_id TEXT PRIMARY KEY,
                question_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, batch_number INTEGER,
                chat_id INTEGER, results_message_id INTEGER, question_ids TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS batch_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER,
                user_id INTEGER, username TEXT, score INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
                first_answer_at TEXT, last_answer_at TEXT, submitted_at TEXT,
                UNIQUE(batch_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS scheduled_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER,
                batch_number INTEGER, scheduled_time TEXT,
                status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS poll_map (
                poll_id TEXT PRIMARY KEY, batch_id INTEGER, question_id INTEGER,
                correct_option INTEGER, explanation TEXT, question_index INTEGER
            );
            CREATE TABLE IF NOT EXISTS admin_state (
                admin_id INTEGER PRIMARY KEY, state TEXT NOT NULL, data TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS admin_context (
                admin_id INTEGER PRIMARY KEY, last_text TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tracked_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, msg_type TEXT,
                chat_id INTEGER, message_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, chat_id INTEGER,
                message_type TEXT, text TEXT, file_id TEXT, caption TEXT,
                scheduled_time TEXT, status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
    logger.info("DB initialized at %s", DB_PATH)

def backup_db():
    try:
        dest = os.path.join(BACKUP_DIR, f"studybot_{datetime.now().strftime('%Y-%m-%d')}.db")
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, dest)
            files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".db"))
            for old in files[:-7]: os.remove(os.path.join(BACKUP_DIR, old))
    except Exception: logger.error("Backup failed", exc_info=True)

# ── Chat roles ──
async def set_chat_role(chat_id, role, title="", chat_type="group"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO chat_roles(chat_id,role,title,chat_type) VALUES(?,?,?,?)",
                         (chat_id, role, title, chat_type))
        await db.commit()

async def remove_chat_role(chat_id, role):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_roles WHERE chat_id=? AND role=?", (chat_id, role))
        await db.commit()

async def get_all_chats_by_role(role):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id FROM chat_roles WHERE role=? AND is_active=1", (role,)) as c:
            return [r[0] for r in await c.fetchall()]

async def get_all_role_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id,role,title,chat_type FROM chat_roles ORDER BY role") as c:
            return await c.fetchall()

async def set_chat_active(chat_id, active):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chat_roles SET is_active=? WHERE chat_id=?", (int(active), chat_id))
        await db.commit()

async def register_known_chat(chat_id, title, chat_type):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO known_chats(chat_id,title,chat_type) VALUES(?,?,?)",
                         (chat_id, title or str(chat_id), chat_type))
        await db.commit()

async def remove_known_chat(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM known_chats WHERE chat_id=?", (chat_id,))
        await db.commit()

async def get_known_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id,title,chat_type FROM known_chats ORDER BY title") as c:
            return await c.fetchall()

# ── Questions ──
async def add_question(admin_id, question_text, options, correct_option,
                       explanation, photo_file_id=None, ai_source="unknown"):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO question_queue(admin_id,question_text,options,correct_option,explanation,photo_file_id,ai_source,status) VALUES(?,?,?,?,?,?,?,?)",
            (admin_id, question_text, json.dumps(options), correct_option, explanation,
             photo_file_id, ai_source, "confirmed" if correct_option is not None else "pending"))
        await db.commit()
        return cur.lastrowid

async def get_queue_questions():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM question_queue WHERE status IN ('pending','confirmed','needs_review') ORDER BY created_at") as c:
            return await c.fetchall()

async def confirm_question_in_db(q_id, correct_option, explanation):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE question_queue SET correct_option=?,explanation=?,status='confirmed' WHERE id=?",
            (correct_option, explanation, q_id))
        await db.commit()
    logger.info("[QUESTION CONFIRMED] QID=%d ANSWER=%s", q_id, chr(65+correct_option))

async def mark_question_needs_review(q_id, reason="AI failed"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE question_queue SET status='needs_review',explanation=? WHERE id=?",
                         (reason, q_id))
        await db.commit()

async def mark_questions_sent(q_ids):
    if not q_ids: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE question_queue SET status='sent' WHERE id IN ({','.join('?'*len(q_ids))})", q_ids)
        await db.commit()

async def clear_queue():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM question_queue WHERE status IN ('pending','confirmed','needs_review')")
        await db.commit()

async def get_question_by_id(q_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM question_queue WHERE id=?", (q_id,)) as c:
            return await c.fetchone()

# ── Admin confirm polls (poll_id → question_id mapping) ──
async def add_confirm_poll(poll_id, question_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO admin_confirm_polls(poll_id,question_id) VALUES(?,?)",
                         (poll_id, question_id))
        await db.commit()
    logger.info("[CONFIRM POLL STORED] POLL_ID=%s QID=%d", poll_id, question_id)

async def get_confirm_poll(poll_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT question_id FROM admin_confirm_polls WHERE poll_id=?", (poll_id,)) as c:
            row = await c.fetchone()
            return row[0] if row else None

async def delete_confirm_poll(poll_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admin_confirm_polls WHERE poll_id=?", (poll_id,))
        await db.commit()

# ── Admin state ──
async def set_admin_state(admin_id, state, data=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO admin_state(admin_id,state,data,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)",
            (admin_id, state, json.dumps(data or {})))
        await db.commit()

async def get_admin_state(admin_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT state,data FROM admin_state WHERE admin_id=?", (admin_id,)) as c:
            row = await c.fetchone()
            return (row[0], json.loads(row[1])) if row else (None, {})

async def clear_admin_state(admin_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admin_state WHERE admin_id=?", (admin_id,))
        await db.commit()

async def set_admin_last_text(admin_id, text):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO admin_context(admin_id,last_text,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)",
                         (admin_id, text))
        await db.commit()

async def get_admin_last_text(admin_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_text FROM admin_context WHERE admin_id=?", (admin_id,)) as c:
            row = await c.fetchone()
            return row[0] if row else None

async def clear_admin_last_text(admin_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admin_context WHERE admin_id=?", (admin_id,))
        await db.commit()

# ── Batches ──
async def get_last_batch_number():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT MAX(batch_number) FROM batches") as c:
            row = await c.fetchone()
            return row[0] or 0

async def create_batch(batch_number, chat_id, question_ids):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO batches(batch_number,chat_id,question_ids) VALUES(?,?,?)",
                               (batch_number, chat_id, json.dumps(question_ids)))
        await db.commit()
        return cur.lastrowid

async def set_batch_results_message(batch_id, message_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE batches SET results_message_id=? WHERE id=?", (message_id, batch_id))
        await db.commit()

async def get_batch(batch_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)) as c:
            return await c.fetchone()

async def upsert_score(batch_id, user_id, username, correct, total):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO batch_scores(batch_id,user_id,username,score,total,first_answer_at,last_answer_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(batch_id,user_id) DO UPDATE SET
               score=score+excluded.score,total=excluded.total,username=excluded.username,
               last_answer_at=excluded.last_answer_at,
               first_answer_at=COALESCE(first_answer_at,excluded.first_answer_at)""",
            (batch_id, user_id, username, 1 if correct else 0, total, now, now))
        await db.commit()

async def get_batch_scores(batch_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT username,score,total,first_answer_at,submitted_at FROM batch_scores WHERE batch_id=? ORDER BY score DESC",
            (batch_id,)) as c:
            return await c.fetchall()

async def mark_batch_submitted(batch_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE batch_scores SET submitted=1 WHERE batch_id=? AND user_id=?",
            (batch_id, user_id))
        await db.commit()

async def add_poll_map(poll_id, batch_id, question_id, correct_option, explanation, question_index):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO poll_map VALUES(?,?,?,?,?,?)",
                         (poll_id, batch_id, question_id, correct_option, explanation, question_index))
        await db.commit()

async def get_poll_map(poll_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM poll_map WHERE poll_id=?", (poll_id,)) as c:
            return await c.fetchone()

# ── Tracked messages ──
async def track_message(msg_type, chat_id, message_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO tracked_messages(msg_type,chat_id,message_id) VALUES(?,?,?)",
                         (msg_type, chat_id, message_id))
        await db.commit()

async def get_tracked_messages():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id,message_id FROM tracked_messages") as c:
            return await c.fetchall()

async def clear_tracked_messages():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tracked_messages")
        await db.commit()

# ── Scheduled messages ──
async def add_scheduled_message(admin_id, chat_id, message_type, scheduled_time,
                                 text=None, file_id=None, caption=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO scheduled_messages(admin_id,chat_id,message_type,text,file_id,caption,scheduled_time) VALUES(?,?,?,?,?,?,?)",
            (admin_id, chat_id, message_type, text, file_id, caption, scheduled_time))
        await db.commit()
        return cur.lastrowid

async def get_due_scheduled_messages():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM scheduled_messages WHERE status='pending' AND scheduled_time<=?", (now,)) as c:
            return await c.fetchall()

async def mark_scheduled_sent(msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE scheduled_messages SET status='sent' WHERE id=?", (msg_id,))
        await db.commit()

async def get_pending_scheduled_messages():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM scheduled_messages WHERE status='pending' ORDER BY scheduled_time") as c:
            return await c.fetchall()

async def delete_scheduled_message(msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM scheduled_messages WHERE id=?", (msg_id,))
        await db.commit()
    logger.info("Scheduled message %d deleted", msg_id)

# ── Batch timing ──
async def record_first_answer(batch_id, user_id, username):
    """Record when user answers their first question in a batch."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""UPDATE batch_scores SET first_answer_at=?
            WHERE batch_id=? AND user_id=? AND first_answer_at IS NULL""",
            (now, batch_id, user_id))
        await db.commit()

async def record_submit(batch_id, user_id):
    """Record when user taps Submit."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""UPDATE batch_scores SET submitted_at=?
            WHERE batch_id=? AND user_id=?""", (now, batch_id, user_id))
        await db.commit()

async def get_user_batch_score(batch_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT username,score,total,first_answer_at,submitted_at FROM batch_scores WHERE batch_id=? AND user_id=?",
            (batch_id, user_id)) as c:
            return await c.fetchone()

# ── Scheduled batches ──
async def add_scheduled_batch(admin_id, chat_id, question_ids, batch_number, scheduled_time):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO scheduled_messages(admin_id,chat_id,message_type,text,scheduled_time) VALUES(?,?,?,?,?)",
            (admin_id, chat_id, "batch", json.dumps({"batch_number": batch_number, "question_ids": question_ids}), scheduled_time))
        await db.commit()
        return cur.lastrowid

async def get_due_scheduled_batches():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM scheduled_messages WHERE status='pending' AND message_type='batch' AND scheduled_time<=?",
            (now,)) as c:
            return await c.fetchall()

# ── Batch timing ──

async def update_question_text(q_id, new_text):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE question_queue SET question_text=? WHERE id=?", (new_text, q_id))
        await db.commit()
    logger.info("[QUESTION EDITED] QID=%d", q_id)
