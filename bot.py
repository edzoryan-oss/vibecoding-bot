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
logger = logging.getLogger(__name__)

# ==== СИСТЕМНИЙ ПРОМПТ (розширений, щоб бот "пам’ятав", хто він) ====
SYSTEM_PROMPT = (
    "Ти — офіційний асистент української спільноти Vibe-Coding (Telegram-чат). "
    "Місія спільноти: «вайб-кодинг» — людина формулює ідею, ШІ допомагає робити готовий результат "
    "(скрипти, програми, відео, аудіо та інші продукти). "
    "Головні ролі бота: 1) відповідати на запити учасників українською; "
    "2) допомагати з ідеями/кодом; 3) бути доброзичливим модератором тону; "
    "4) за запитом генерувати ілюстрації через API. "
    "Твій стиль: ввічливий, лаконічний, практичний. Дозволена легка (доброзичлива) іронія над "
    "типовими болями програмістів (типу багів, дедлайнів, кави), але без токсичності чи принижень. "
    "Додаткові правила: "
    "- Поважай усіх учасників, не використовуй мову ненависті чи дискримінацію. "
    "- Уникай образ, приниження, персональних атак або закликів до насильства. "
    "- Якщо користувач просить картинку, коротко перефразу й створи ілюстрацію. "
    "- Якщо запит порушує правила (наприклад, хейт/насильство), ввічливо відмовся і запропонуй безпечну альтернативу. "
    "- Для групових чатів відповідай лише коли тебе явно згадали тегом або звернулися словом «бот», "
    "а також на команди /img та /imgtest. "
    "Про спільноту: тут ділимося досвідом, ідеями, кодом і допомагаємо одне одному. "
    "Сайт спільноти: vibe-coding.com.ua (точка збору вайб-кодерів)."
)

HELP_TEXT = (
    "👋 Привіт у Vibe-Coding!\n\n"
    "Команди:\n"
    "• /img <опис> — згенерувати ілюстрацію (наприклад: /img кіт у мультяшному стилі)\n"
    "• /imgtest — тестове зображення для перевірки\n"
    "• /help — ця підказка\n\n"
    "У групі бот відповідає, якщо тегнути @бота або почати повідомлення зі слова «бот»."
)

START_TEXT = (
    "Привіт! Я асистент спільноти Vibe-Coding 🇺🇦\n"
    "Я допомагаю з ідеями, кодом і можу згенерувати ілюстрації.\n"
    "Спробуй: /img кіт у мультяшному стилі\n"
    "Або напиши «бот підкажи як…» у групі."
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
    Спочатку gpt-image-1; якщо недоступна на openai==0.28 чи в акаунті — фолбек на dall-e-3.
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
        model_used = resp.get("model", "gpt-image-1")
        logger.info("OpenAI image model used: %s", model_used)
        return resp["data"][0]["url"], model_used
    except Exception as e:
        logger.warning("gpt-image-1 failed: %s — falling back to dall-e-3", e)

    logger.info("Trying dall-e-3...")
    resp = openai.Image.create(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        n=1
    )
    model_used = resp.get("model", "dall-e-3")
    logger.info("OpenAI image model used: %s", model_used)
    return resp["data"][0]["url"], model_used

# ==== КОМАНДИ ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(START_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def imgtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("IMGTEST TRIGGERED by %s", update.effective_user.id)
    prompt = "cute cartoon turtle with big eyes, bright colors, simple background, 2D illustration"
    try:
        url, model_used = _image_create_url(prompt)
        img_bytes = _download_to_bytes(url)
        bio = io.BytesIO(img_bytes); bio.name = "image.png"; bio.seek(0)
        await update.message.reply_photo(bio, caption=f"Готово ✅ (тест, модель: {model_used})")
        logger.info("Image OK | model=%s | prompt='%s'", model_used, prompt)
    except Exception as e:
        logger.exception("Image gen error (imgtest): %s", e)
        await update.message.reply_text(f"Помилка тестової генерації: {e}")

async def img_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args).strip()
    logger.info("IMG TRIGGERED by %s | prompt='%s'", update.effective_user.id, prompt)
    if not prompt:
        await update.message.reply_text("Напиши опис: /img <що намалювати>\nПриклад: /img кіт у мультяшному стилі")
        return

    try:
        url, model_used = _image_create_url(prompt)
        img_bytes = _download_to_bytes(url)
        bio = io.BytesIO(img_bytes); bio.name = "image.png"; bio.seek(0)
        await update.message.reply_photo(bio, caption=f"Готово ✅")
        logger.info("Image OK | model=%s | prompt='%s'", model_used, prompt)
    except Exception as e:
        logger.exception("Image gen error: %s", e)
        await update.message.reply_text(f"Не вийшло створити зображення 😕\nПомилка: {e}")

# ==== ТЕКСТОВІ ПОВІДОМЛЕННЯ ====
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()

    # В групі відповідаємо тільки якщо тегнули або звернулись "бот ..."
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
    app.add_handler(CommandHandler("imgtest", imgtest_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
