# Study Bot — Setup & Deployment Guide

## Files Structure
```
studybot/
├── main.py              # Bot entry point
├── config.py            # All settings
├── requirements.txt     # Dependencies
├── .env.example         # API keys template
├── database/
│   └── db.py            # SQLite database
├── handlers/
│   ├── admin.py         # Admin question pipeline
│   ├── quiz.py          # Poll sending & scoring
│   └── tracker.py       # Daily study tracker
└── utils/
    ├── groq_client.py   # Groq AI (OCR + answers)
    ├── tavily_client.py # Web search
    └── ai_pipeline.py   # Smart AI routing
```

---

## Step 1 — Get Your API Keys

### Telegram Bot Token
1. Open Telegram, search @BotFather
2. Send /newbot
3. Choose a name and username
4. Copy the token

### Your Telegram User ID
1. Search @userinfobot on Telegram
2. Send /start
3. Copy your ID number

### Groq API Key
1. Go to console.groq.com
2. Sign up free
3. Create API key → copy it

### Tavily API Key
1. Go to tavily.com
2. Sign up free
3. Copy your API key

---

## Step 2 — GitHub Setup

1. Create account at github.com
2. Click "New Repository" → name it "studybot"
3. Upload all the bot files
4. Done

---

## Step 3 — Render.com Deployment

1. Create account at render.com (no card needed)
2. Click "New" → "Web Service"
3. Connect your GitHub account
4. Select your studybot repository
5. Set these:
   - Environment: Python 3
   - Build Command: pip install -r requirements.txt
   - Start Command: python main.py
6. Add Environment Variables:
   - TELEGRAM_TOKEN = your token
   - GROQ_API_KEY = your key
   - TAVILY_API_KEY = your key
   - ADMIN_USER_ID = your user id number
7. Click Deploy

---

## Step 4 — UptimeRobot (Keep Bot Alive 24/7)

1. Create account at uptimerobot.com (free)
2. Click "Add New Monitor"
3. Type: HTTP(s)
4. URL: your Render app URL
5. Interval: Every 5 minutes
6. Save — bot stays alive forever

---

## Step 5 — First Time Bot Setup

1. Add bot to your Quiz Channel as Admin
2. Add bot to your Study Group as Admin
3. In Quiz Channel, type: /setup → tap "Quiz Channel"
4. In Study Group, type: /setup → tap "Study Group"
5. DM the bot /start to register as admin

---

## How To Use

### Adding Questions
1. Forward any question (photo/poll/text) to bot privately
2. Bot reads it with AI, shows you the answer
3. Tap Confirm or Override
4. Repeat for all questions

### Sending Questions to Channel
1. DM bot /admin
2. Tap "Preview Queue" to review
3. Tap "Forward All"
4. Bot sends all questions to quiz channel

### Daily Tracker
- 9:00 PM — Bot pings study group
- Members DM bot /checkin and follow steps
- 10:00 PM — Bot posts summary in group

### Commands
| Command | Where | What |
|---------|-------|------|
| /admin | DM | Admin control panel |
| /setup | Channel/Group | Set chat role |
| /checkin | DM | Log today's study |
| /mystats | DM | Your weekly stats (auto-deletes in 5min) |
| /leaderboard | Channel/Group | Quiz scores |
