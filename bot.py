import os
import io
import base64
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
    "Якщо користувач просить зображення — прийми опис і створи ілюстрацію. "
    "В разі порушень — ввічливо відмовляй і пропонуй безпечну альтернативу."
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

# Просте «пом’якшення» промптів, щоб уникати відмов модерації
SOFTEN_MAP = {
    r"\bб'?ється\b": "змагається",
    r"\bбити\b": "змагатися",
    r"\bбитва\b": "поєдинок",
}
BANNED_REAL_PERSONS = [
    # не зберігаємо образливі вирази; лише кілька прикладів реальних осіб/політиків
    "янукович", "путін", "зеленський", "ілон маск", "маск", "байден", "трамп"
]

def soften_prompt(p: str) -> str:
    t = p.strip()
    # прибрати згадки реальних осіб -> узагальнити
    low = t.lower()
    if any(name in low for name in BANNED_REAL_PERSONS):
        t = re.sub("|".join(BANNED_REAL_PERSONS), "відомий діяч (вигаданий образ)", t, flags=re.IGNORECASE)
        t += ", мультяшний/карикатурний стиль, без схожості з реальною особою"
    # заміни «насильницьких» слів на нейтральні
    for pat, rep in SOFTEN_MAP.items():
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    # додаткові підказки для якості
    if "мультяш" not in t.lower() and "карикатур" not in t.lower() and "ілюстрац" not in t.lower():
        t += ", ілюстрація, дружній мультяшний стиль"
    return t

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
    """Генеруємо картинку як base64 і відправляємо її як фото."""
    try:
        safe_prompt = soften_prompt(prompt)
        img_resp = openai.Image.create(
            model="gpt-image-1",
            prompt=safe_prompt,
            size="1024x1024",
            n=1,
            response_format="b64_json"
        )
        b64 = img_resp["data"][0]["b64_json"]
        img_bytes = base64.b64decode(b64)

        bio = io.BytesIO(img_bytes)
        bio.name = "image.png"
        bio.seek(0)
        await update.message.reply_photo(bio, caption="Готово ✅")

        logging.info("Image OK | prompt='%s' | model=%s", safe_prompt, img_resp.get("model", "gpt-image-1"))
    except Exception as e:
        logging.exception("Image gen error: %s", e)
        await update.message.reply_text(
            "Не вийшло створити зображення 😕 Спробуй коротший або м’якший опис (мультяшний стиль)."
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
        # відрізаємо ключове слово і лишаємо опис
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

# ====== ОБРОБКА ПОМИЛОК ТГ-БІБЛІОТЕКИ ======================================

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
    # drop_pending_updates — щоб не навалювалися старі апдейти після перезапуску
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
