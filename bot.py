import os
import io
import re
import logging
import requests
from datetime import datetime, timedelta
from collections import defaultdict

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
import openai

# ==== КЛЮЧІ ====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# ==== ЛОГИ ====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==== КОНФІГ ОБМЕЖЕНЬ ====
# скільки зображень на день може згенерувати кожен користувач (0 або менше = без денного ліміту)
IMG_LIMIT_PER_DAY = int(os.getenv("IMG_LIMIT_PER_DAY", "2"))

# кулдаун між генераціями на користувача (секунди). за замовчуванням 5 хв = 300с
IMG_COOLDOWN_SEC = int(os.getenv("IMG_COOLDOWN_SEC", "20"))

# заборонені слова/теми
BANNED_WORDS = [
    "янукович", "батон", "дурка", "дурку", "дуркувати",
    # додай інші слова на свій розсуд
]

# ==== ПАМ'ЯТЬ ДЛЯ ЛІЧИЛЬНИКІВ (RAM, без Redis) ====
# Денний ліміт: { (user_id, YYYYMMDD): count }
IMG_COUNTER_RAM = defaultdict(int)
# Кулдаун: { user_id: datetime_utc_останньої_успішної_генерації }
LAST_IMG_TS_RAM: dict[int, datetime] = {}

def _today_tag() -> str:
    return datetime.utcnow().strftime("%Y%m%d")

def _img_quota_key(user_id: int) -> str:
    return f"{user_id}:{_today_tag()}"

def _fmt_tdelta(seconds: int) -> str:
    # красивий формат "X хв Y с"
    m, s = divmod(max(0, seconds), 60)
    if m and s:
        return f"{m} хв {s} с"
    if m:
        return f"{m} хв"
    return f"{s} с"

def _check_cooldown(user_id: int) -> tuple[bool, int]:
    """
    Перевіряє, чи минув кулдаун.
    Повертає (allow, remain_seconds).
    """
    if IMG_COOLDOWN_SEC <= 0:
        return True, 0
    last_ts = LAST_IMG_TS_RAM.get(user_id)
    if not last_ts:
        return True, 0
    now = datetime.utcnow()
    delta = (now - last_ts).total_seconds()
    if delta >= IMG_COOLDOWN_SEC:
        return True, 0
    remain = int(IMG_COOLDOWN_SEC - delta)
    return False, remain

def _mark_cooldown(user_id: int):
    LAST_IMG_TS_RAM[user_id] = datetime.utcnow()

def inc_and_check_img_quota(user_id: int) -> tuple[bool, int]:
    """
    Збільшує денний лічильник і повертає (allow, left).
    Якщо денний ліміт вимкнений (<=0) — повертає (True, -1).
    Якщо ліміт вичерпано — (False, 0) і лічильник НЕ збільшує.
    """
    limit = IMG_LIMIT_PER_DAY
    if limit <= 0:
        return True, -1  # без денного ліміту
    key = _img_quota_key(user_id)
    current = IMG_COUNTER_RAM[key]
    if current >= limit:
        return False, 0
    IMG_COUNTER_RAM[key] = current + 1
    left = limit - IMG_COUNTER_RAM[key]
    return True, left

def prompt_has_banned_words(text: str) -> bool:
    t = (text or "").lower()
    for w in BANNED_WORDS:
        if w in t:
            return True
    return False

# ==== ПРОМПТ ТА ДОВІДКА ====
SYSTEM_PROMPT = (
    "Ти — помічник українського чату Vibe-Coding. "
    "Відповідай українською, чемно та лаконічно. "
    "Трохи дружньої іронії ок, без токсичності."
)

def _help_text() -> str:
    daily = "без ліміту на день" if IMG_LIMIT_PER_DAY <= 0 else f"ліміт на день: {IMG_LIMIT_PER_DAY}"
    cd = f"{_fmt_tdelta(IMG_COOLDOWN_SEC)} між запитами"
    return (
        "Команди:\n"
        "/img <опис> — згенерувати ілюстрацію "
        f"({daily}, кулдаун: {cd})\n"
        "/help — допомога"
    )

# ==== УТИЛІТИ ДЛЯ ЗОБРАЖЕНЬ ====
def _download_to_bytes(url: str) -> bytes:
    logger.info("Downloading image from URL...")
    r = requests.get(url, timeout=45)
    r.raise_for_status()
    logger.info("Downloaded image bytes: %s", len(r.content))
    return r.content

def _image_create_url(prompt: str) -> tuple[str, str]:
    """
    Спочатку пробуємо gpt-image-1; якщо недоступно — фолбек на dall-e-3.
    Повертає (url, model_name).
    """
    try:
        logger.info("Trying gpt-image-1...")
        resp = openai.Image.create(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
            n=1
        )
        return resp["data"][0]["url"], resp.get("model", "gpt-image-1")
    except Exception as e:
        logger.warning("gpt-image-1 failed: %s — falling back to dall-e-3", e)

    resp = openai.Image.create(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        n=1
    )
    return resp["data"][0]["url"], resp.get("model", "dall-e-3")

# ==== КОМАНДИ ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Для картинки — /img <опис>. Для довідки — /help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_help_text())

async def img_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prompt = " ".join(context.args).strip()

    if not prompt:
        await update.message.reply_text(
            "Напиши опис: /img <що намалювати>\nПриклад: /img кіт у мультяшному стилі"
        )
        return

    # 0) фільтр небажаних промптів
    if prompt_has_banned_words(prompt):
        await update.message.reply_text(
            "Це більше схоже на мем. Для креативних жартиків у нас окремий простір 😉\n"
            "Тут тримаємо фокус на серйозному вайб-кодингу."
        )
        return

    # 1) перевірка кулдауну (не частіше, ніж 1 картинка на N секунд)
    allow_cd, remain_sec = _check_cooldown(user_id)
    if not allow_cd:
        await update.message.reply_text(
            f"Занадто часто 😊 Зачекай ще {_fmt_tdelta(remain_sec)} перед наступною генерацією."
        )
        return

    # 2) денний ліміт (можна вимкнути IMG_LIMIT_PER_DAY<=0)
    allow_daily, left = inc_and_check_img_quota(user_id)
    if not allow_daily:
        await update.message.reply_text(
            "Твій ліміт на зображення на сьогодні вичерпано 😕\n"
            "Повернись завтра або сформулюй технічне питання без картинки."
        )
        return

    # 3) генеруємо
    try:
        url, model_used = _image_create_url(prompt)
        img_bytes = _download_to_bytes(url)
        bio = io.BytesIO(img_bytes); bio.name = "image.png"; bio.seek(0)
        suffix_daily = "" if left < 0 else f"  (залишилось на сьогодні: {left})"
        await update.message.reply_photo(bio, caption=f"Готово ✅{suffix_daily}")
        logger.info("Image OK | model=%s | prompt='%s' | left=%s", model_used, prompt, left)
        _mark_cooldown(user_id)  # оновлюємо таймер кулдауну тільки після успішної генерації
    except Exception as e:
        logger.exception("Image gen error: %s", e)
        await update.message.reply_text(
            "Не вийшло створити зображення 😕 Спробуй інший опис (наприклад, «мультяшний стиль»)."
        )

# ==== ТЕКСТОВІ ПОВІДОМЛЕННЯ ====
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()

    # відповідаємо в групі лише якщо тегнули або написали "бот ..." на початку
    bot_username = (context.bot.username or "").lower()
    entities = update.message.entities or []
    mentioned_bot = any(
        e.type == "mention" and
        text[e.offset:e.offset + e.length].lower() == f"@{bot_username}"
        for e in entities
    )
    starts_with_word = text.lower().startswith("бот")
    if update.effective_chat.type != "private" and not (mentioned_bot or starts_with_word):
        return

    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0.4
        )
        reply = completion["choices"][0]["message"]["content"].strip()
        await update.message.reply_text(reply)
        usage = completion.get("usage", {})
        logger.info("Chat OK | model=%s | prompt=%s | completion=%s",
                    completion.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens"))
    except Exception as e:
        logger.exception("Text gen error: %s", e)
        await update.message.reply_text("Вибач, сталася помилка на стороні ШІ. Спробуй ще раз 🙏")

# ==== ОБРОБКА ПОМИЛОК ====
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Bot error: %s", context.error)

# ==== ЗАПУСК ====
def main():
    logger.info("Starting bot...")
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is missing!")
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY is missing!")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

