import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import openai

# 🔑 Keys
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)

SYSTEM_PROMPT = (
    "Ти — офіційний помічник українського чату Vibe-Coding. "
    "Відповідай українською, чемно і по суті, з легкою іронією про типові болі програмістів (без токсичності). "
    "Не допускай мови ненависті чи закликів до насильства. "
    "Якщо просять картинку — приймай короткий опис і генеруй її."
)

HELP_TEXT = (
    "👋 Як користуватись ботом:\n"
    "• Звертайся тегом @імʼя_бота або починай повідомлення зі слова «бот».\n"
    "• Щоб намалювати: /img <опис> або «згенеруй картинку <опис>», «намалюй <опис>».\n"
    "Приклад: /img лого у стилі мінімалізм, синьо-жовті кольори.\n"
)

RULES_TEXT = (
    "Правила Vibe-Coding: повага, конструктив, українська мова. "
    "Без мови ненависті та пропаганди агресії. Допомагаємо одне одному, ділимось ідеями та кодом."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я GPT-бот Vibe-Coding. Напиши «бот ...» або тегни @мене. Для картинок: /img <опис>."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(RULES_TEXT)

async def generate_image_and_reply(update: Update, prompt: str):
    try:
        # OpenAI Images API (для openai==0.28 — Image.create)
        img_resp = openai.Image.create(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024"
        )
        url = img_resp["data"][0]["url"]
        await update.message.reply_photo(url, caption="Готово ✅")
    except Exception as e:
        logging.exception("Image gen error: %s", e)
        await update.message.reply_text("Не вийшло створити зображення 😕 Спробуй переформулювати опис.")

async def img_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Напиши опис: /img <що намалювати>")
        return
    await generate_image_and_reply(update, prompt)

def is_image_request(text: str) -> bool:
    t = text.lower().strip()
    return t.startswith("згенеруй картинку") or t.startswith("намалюй") or t.startswith("створи картинку")

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_message = update.message.text.strip()

    # Тригер: тег саме нашого бота або початок "бот"
    bot_username = (context.bot.username or "").lower()
    entities = update.message.entities or []
    mentioned_bot = any(
        e.type == "mention" and
        user_message[e.offset:e.offset + e.length].lower() == f"@{bot_username}"
        for e in entities
    )
    starts_with_word = user_message.lower().startswith("бот")

    if not (mentioned_bot or starts_with_word or is_image_request(user_message)):
        return

    # Якщо просили картинку — генеруємо і виходимо
    if is_image_request(user_message):
        # вирізаємо ключове слово і лишаємо опис
        for kw in ["згенеруй картинку", "намалюй", "створи картинку"]:
            if user_message.lower().startswith(kw):
                prompt = user_message[len(kw):].strip(" :,-")
                break
        if not prompt:
            await update.message.reply_text("Додай опис до запиту на картинку 🙂")
            return
        await generate_image_and_reply(update, prompt)
        return

    # Інакше — звичайна текстова відповідь
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
        # (необов’язково) залогувати модель і токени
        usage = completion.get("usage", {})
        logging.info("OpenAI model=%s prompt=%s completion=%s",
                     completion.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens"))
    except Exception as e:
        logging.exception("OpenAI error: %s", e)
        await update.message.reply_text("Вибач, сталася помилка на стороні ШІ. Спробуй ще раз 🙏")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
