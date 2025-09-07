import os
import io
import logging
import requests

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

# ==== СИСТЕМНИЙ ПРОМПТ (для текстових відповідей) ====
SYSTEM_PROMPT = (
    "Ти — помічник українського чату Vibe-Coding. "
    "Відповідай українською, чемно та лаконічно. "
    "Можна легка іронія про типові 'болі' програмістів, без токсичності."
)

# ==== ДОПОМОГА/ПРАВИЛА ====
HELP_TEXT = (
    "Команди:\n"
    "/img <опис> — згенерувати ілюстрацію (обов'язково починай з /img)\n"
    "/help — допомога"
)

# ==== УТИЛІТИ ДЛЯ ЗОБРАЖЕНЬ ====
def _download_to_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=45)
    r.raise_for_status()
    return r.content

def _image_create_url(prompt: str) -> tuple[str, str]:
    """
    Спочатку пробуємо gpt-image-1 (якщо доступна у твоєму акаунті/API-шлюзі),
    якщо падає — переходимо на dall-e-3.
    Повертає (url, model_name).
    """
    # 1) gpt-image-1
    try:
        resp = openai.Image.create(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
            n=1
        )
        return resp["data"][0]["url"], resp.get("model", "gpt-image-1")
    except Exception as e:
        logging.warning("gpt-image-1 failed: %s — falling back to dall-e-3", e)

    # 2) dall-e-3 (стабільно з openai==0.28.0)
    resp = openai.Image.create(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        n=1
    )
    return resp["data"][0]["url"], resp.get("model", "dall-e-3")

# ==== КОМАНДИ ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Для картинки використовуй: /img <опис>. Для довідки: /help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def img_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Напиши опис: /img <що намалювати>\nПриклад: /img кіт у мультяшному стилі")
        return

    try:
        url, model_used = _image_create_url(prompt)
        img_bytes = _download_to_bytes(url)
        bio = io.BytesIO(img_bytes); bio.name = "image.png"; bio.seek(0)
        await update.message.reply_photo(bio, caption=f"Готово ✅")
        logging.info("Image OK | model=%s | prompt='%s'", model_used, prompt)
    except Exception as e:
        logging.exception("Image gen error: %s", e)
        await update.message.reply_text("Не вийшло створити зображення 😕 Спробуй інший опис (додай «мультяшний стиль»).")

# ==== ТЕКСТОВІ ПОВІДОМЛЕННЯ ====
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()

    # ТРИГЕРИ: тег або початок зі слова "бот"
    bot_username = (context.bot.username or "").lower()
    entities = update.message.entities or []
    mentioned_bot = any(
        e.type == "mention" and
        text[e.offset:e.offset + e.length].lower() == f"@{bot_username}"
        for e in entities
    )
    starts_with_word = text.lower().startswith("бот")
    if not (mentioned_bot or starts_with_word):
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
        logging.info("Chat OK | model=%s | prompt=%s | completion=%s",
                     completion.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens"))
    except Exception as e:
        logging.exception("Text gen error: %s", e)
        await update.message.reply_text("Вибач, сталася помилка на стороні ШІ. Спробуй ще раз 🙏")

# ==== ОБРОБКА ПОМИЛОК ====
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Bot error: %s", context.error)

# ==== ЗАПУСК ====
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
