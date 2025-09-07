import os
import io
import logging
import re

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
import openai

# ====== КЛЮЧІ ===============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# ====== ЛОГИ ================================================================
logging.basicConfig(level=logging.INFO)

# ====== СИСТЕМНИЙ ПРОМПТ ====================================================
SYSTEM_PROMPT = (
    "Ти — офіційний помічник українського чату Vibe-Coding. "
    "Мета спільноти: вайб-кодинг — ідеї формулює людина, код пише ШІ. "
    "Відповідай українською, чемно, лаконічно і по суті. Дозволена легка іронія "
    "щодо типових «болей» програмістів без токсичності. "
    "Не допускай мови ненависті чи закликів до насильства. "
    "Якщо користувач просить зображення — прийми опис і створи ілюстрацію."
)

HELP_TEXT = (
    "👋 Як користуватись ботом:\n"
    "• Звертайся тегом @імʼя_бота або починай повідомлення зі слова «бот».\n"
    "• Створити картинку: /img <опис>\n"
    "  або фрази: «згенеруй картинку …», «намалюй …», «створи картинку …».\n"
    "Приклад: /img мінімалістичне лого у синьо-жовтих кольорах."
)

RULES_TEXT = (
    "Правила Vibe-Coding: повага, українська мова, конструктив. "
    "Без мови ненависті, дискримінації та закликів до насильства. "
    "Ділимось ідеями, кодом і допомагаємо одне одному. Слава Україні! 🇺🇦"
)

# ====== ДОПОМІЖНІ ФУНКЦІЇ ===================================================

IMAGE_KEYWORDS = (
    "згенеруй картинку", "намалюй", "створи картинку", "генеруй картинку",
    "створи зображення", "згенеруй зображення"
)

def is_image_request(text: str) -> bool:
    t = (text or "").lower().strip()
    return any(t.startswith(k) for k in IMAGE_KEYWORDS)

# ====== КОМАНДИ =============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я GPT-бот Vibe-Coding. Напиши «бот …» або тегни @мене. "
        "Для зображень скористайся /img <опис>."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(RULES_TEXT)

# ====== ГЕНЕРАЦІЯ ЗОБРАЖЕНЬ =================================================

async def generate_image_and_reply(update: Update, prompt: str):
    """Генеруємо картинку і відправляємо її як фото через URL (для openai==0.28)."""
    try:
        img_resp = openai.Image.create(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
            n=1
        )
        url = img_resp["data"][0]["url"]
        await update.message.reply_photo(url, caption="Готово ✅")

        logging.info("Image OK | prompt='%s' | model=%s", prompt, img_resp.get("model", "gpt-image-1"))
    except Exception as e:
        logging.exception("Image gen error: %s", e)
        await update.message.reply_text(
            "Не вийшло створити зображення 😕 Спробуй коротший опис або додай «мультяшний стиль»."
        )

async def img_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Напиши опис: /img <що намалювати>")
        return
    await generate_image_and_reply(update, prompt)

# ====== ЧАТ-ЛОГІКА ==========================================================

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_message = update.message.text.strip()

    # Тригер: тег саме нашого бота або початок зі слова "бот"
    bot_username = (context.bot.username or "").lower()
    entities = update.message.entities or []
    mentioned_bot = any(
        e.type == "mention" and
        user_message[e.offset:e.offset + e.length].lower() == f"@{bot_username}"
        for e in entities
    )
    starts_with_word = user_message.lower().startswith("бот")

    # Якщо це запит на картинку — навіть без тригера відповідаємо
    if is_image_request(user_message):
        lower = user_message.lower()
        for kw in IMAGE_KEYWORDS:
            if lower.startswith(kw):
                prompt = user_message[len(kw):].strip(" :,-")
                break
        else:
            prompt = user_message
        if not prompt:
            await update.message.reply_text("Додай опис до запиту на зображення 🙂")
            return
        await generate_image_and_reply(update, prompt)
        return

    if not (mentioned_bot or starts_with_word):
        return

    # Текстова відповідь
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.4,
        )
        reply = completion["choices"][0]["message"]["content"].strip()
        await update.message.reply_text(reply)

        usage = completion.get("usage", {})
        logging.info("Chat OK | model=%s | prompt=%s | completion=%s",
                     completion.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens"))
    except Exception as e:
        logging.exception("OpenAI error: %s", e)
        await update.message.reply_text("Вибач, сталася тимчасова помилка на стороні ШІ. Спробуй ще раз 🙏")

# ====== ОБРОБКА ПОМИЛОК ====================================================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Bot error: %s", context.error)

# ====== ЗАПУСК ==============================================================

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
