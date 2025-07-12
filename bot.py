"""
tg_gpt_bot — Telegram ↔ ChatGPT
───────────────────────────────
• пересылает сообщения в OpenAI ChatGPT и отвечает
• логирует: Google Sheets → fallback SQLite
• JobQueue шлёт ежедневный «пинг»
"""

import os, logging, datetime, json, base64, aiosqlite
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from openai import AsyncOpenAI        # новый клиент

# ── 1. окружение ───────────────────────────────────────────
load_dotenv()                         # читает .env локально

BOT_TOKEN      = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID  = int(os.getenv("ADMIN_CHAT_ID", 0))     # 0 → пинг отключён
GSHEET_ID      = os.getenv("GSHEET_ID")
CREDS_B64      = os.getenv("GOOGLE_CREDS_JSON")

if not BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("BOT_TOKEN или OPENAI_API_KEY не заданы")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ── 2. логирование ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("tg_gpt_bot")

# ── 3. Google-Sheets (опц.) ───────────────────────────────
SHEET = None
if GSHEET_ID and CREDS_B64:
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            json.loads(base64.b64decode(CREDS_B64)),
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        SHEET = gspread.authorize(creds).open_by_key(GSHEET_ID).sheet1
        log.info("Google Sheets logging enabled")
    except Exception as e:
        log.warning("Sheets init failed: %s", e)

# ── 4. helper GPT ─────────────────────────────────────────
async def ask_gpt(prompt: str) -> str:
    r = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system",
             "content": "Ты дружелюбный, минималистичный скрам-мастер"},
            {"role": "user", "content": prompt}
        ]
    )
    return r.choices[0].message.content.strip()

# ── 5.  хэндлеры ──────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот-проводник ↔ ChatGPT.\n"
        "Пиши вопрос — пришлю ответ.\n"
        "Якорь: «Сейчас делаю только один щелчок»."
    )

async def relay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.message.text
    a = await ask_gpt(q)
    await update.message.reply_text(a)

    ts  = datetime.datetime.utcnow().isoformat()
    usr = update.effective_user.username or ""
    if SHEET:
        try:
            SHEET.append_row([ts, usr, q, a], value_input_option="RAW")
        except Exception as e:
            log.warning("Sheets append failed: %s", e)
    else:
        async with aiosqlite.connect("log.db") as db:
            await db.execute("CREATE TABLE IF NOT EXISTS log(ts,user,q,a)")
            await db.execute("INSERT INTO log VALUES (?,?,?,?)", (ts, usr, q, a))
            await db.commit()

# ── 6. ежедневный пинг ────────────────────────────────────
async def daily_ping(ctx: ContextTypes.DEFAULT_TYPE):
    if ADMIN_CHAT_ID:
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text= "Вопрос 1: Что ты сделал вчера/с последнего входа?\nфикс прогресса\nВопрос 
                    2: Что ты собираешься сделать сегодня?\nцель на день/спринт\nВопрос 
                    3: Что мешает тебе идти дальше?\nфиксация блокеров / саморефлексия"
        )

# ── 7. старт приложения ──────────────────────────────────
def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay))

    # JobQueue работает, если PTB установлен с extras [job-queue]
    app.job_queue.run_daily(
        daily_ping,
        time=datetime.time(hour=9, tzinfo=datetime.timezone.utc)
    )

    log.info("Bot started — waiting for messages…")
    app.run_polling()          # не запускай вторую копию параллельно!

if __name__ == "__main__":
    main()
