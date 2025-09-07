import os
import io
import re
import logging
import requests
from collections import defaultdict, deque

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

# ==== ПАМ'ЯТЬ (в RAM) ====
# Для кожного chat_id зберігаємо deque з останніх N повідомлень (user/assistant).
MAX_TURNS = int(os.getenv("MAX_TURNS", "12"))  # по черзі: user, assistant, ...
CHAT_MEMORY: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_TURNS * 2))

def remember(chat_id: int, role: str, content: str):
    CHAT_MEMORY[chat_id].append({"role": role, "content": content})

def get_memory(chat_id: int):
    return list(CHAT_MEMORY[chat_id])

def clear_memory(chat_id: int):
    CHAT_MEMORY[chat_id].clear()

# ==== СИСТЕМНИЙ ПРОМПТ (жорстко фіксує роль) ====
SYSTEM_PROMPT = (
    "Ти — офіційний асистент української спільноти Vibe-Coding (Telegram-чат). "
    "Місія: «вайб-кодинг» — людина формулює ідею, ШІ допомагає доводити до готового результату "
    "(скрипти, програми, відео, аудіо та інші продукти). "
    "Роль бота: 1) відповідати українською; 2) допомагати з ідеями/кодом; "
    "3) підтримувати дружній, професійний тон; 4) за запитом генерувати ілюстрації через API. "
    "Стиль: ввічливо, коротко, по суті. Дозволена легка, доброзичлива іронія над "
    "типовими болями програмістів (\"працює на моїй машині\", дедлайни, баги), без токсичності. "
    "Заборонені мова ненависті, дискримінація, заклики до насильства. "
    "У групових чатах відповідай тільки коли звертаються тегом або зі слова «бот», а також на /img та /imgtest. "
    "Про спільноту: ділимось досвідом, ідеями, кодом і допомагаємо одне одному. "
    "Сайт: vibe-coding.com.ua. Відповіді тримай в українському контексті."
)

# ==== СТАТИЧНІ ТЕКСТИ ====
HELP_TEXT = (
    "👋 Vibe-Coding помічник\n\n"
    "Команди:\n"
    "• /about — хто я і навіщо\n"
    "• /img <опис> — згенерувати ілюстрацію (напр.: /img кіт у мультяшному стилі)\n"
    "• /imgtest — тест зображення\n"
    "• /context — показати, що бот пам'ятає (останній контекст)\n"
    "• /reset — очистити контекст для цього чату\n"
    "• /help — довідка\n\n"
    "У групі бот відповідає, якщо тегнути @бота або почати повідомлення зі слова «бот»."
)

START_TEXT = (
    "Привіт! Я асистент спільноти Vibe-Coding 🇺🇦\n"
    "Пам'ятаю контекст у межах цього чату (останній діалог), можу допомогти з кодом і генерити ілюстрації.\n"
    "Команди: /about, /img, /imgtest, /context, /reset, /help."
)

ABOUT_TEXT = (
    "Я — офіційний асистент української спільноти Vibe-Coding. "
    "Ми практикуємо «вайб-кодинг»: ти формулюєш ідею, а ШІ допомагає зробити результат — від скриптів "
    "та застосунків до відео й аудіо. Я відповідаю українською, даю практичні підказки, "
    "інколи доброзичливо піджартовую над вічними проблемами девів, і за потреби генерую ілюстрації через /img. "
    "Сайт: vibe-coding.com.ua. Щоб стерти поточний контекст: /reset."
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

# ==== ХЕЛПЕРИ ДЛЯ "ХТО ТИ" ====
ABOUT_PATTERNS = re.compile(
    r"\b(хто\s+ти|хто\s+такий|для\s+чого\s+ти|навіщо\s+ти|що\s+ти\s+робиш|яка\s+твоя\s+роль)\b",
    re.IGNORECASE
)
def looks_like_about_question(text: str) -> bool:
    return bool(ABOUT_PATTERNS.search((text or "").strip()))

# ==== ЗБІРКА ПРОМПТА З КОНТЕКСТОМ ====
def build_messages(chat_id: int, user_text: str):
    """
    Включає системний промпт + попередню історію цього чату + поточне повідомлення.
    """
    history = get_memory(chat_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Підрізаємо історію, якщо вона занадто велика — deque вже обмежує maxlen
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages

# ==== КОМАНДИ ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(START_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ABOUT_TEXT)

async def context_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mem = get_memory(chat_id)
    if not mem:
        await update.message.reply_text("Контекст порожній. Починай нову розмову 🙂")
        return
    # показуємо останні до 6 пар (12 записів)
    shown = mem[-12:]
    preview = []
    for m in shown:
        role = "👤" if m["role"] == "user" else "🤖"
        text = m["content"]
        if len(text) > 220:
            text = text[:220] + "…"
        preview.append(f"{role} {text}")
    await update.message.reply_text("Поточний контекст:\n\n" + "\n\n".join(preview))

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    clear_memory(chat_id)
    await update.message.reply_text("Ок, контекст для цього чату очищено 🧹")

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
        # Збережемо сам запит користувача як частину контексту (корисно)
        remember(update.effective_chat.id, "user", f"[Запит на зображення] {prompt}")
        remember(update.effective_chat.id, "assistant", "[Надіслано ілюстрацію]")
    except Exception as e:
        logger.exception("Image gen error: %s", e)
        await update.message.reply_text(f"Не вийшло створити зображення 😕\nПомилка: {e}")

# ==== ТЕКСТОВІ ПОВІДОМЛЕННЯ (з пам'яттю) ====
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # В групі відповідаємо тільки якщо тегнули або звернулись "бот ..."
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

    # Фіксований about без LLM
    if looks_like_about_question(text):
        await update.message.reply_text(ABOUT_TEXT)
        # теж кладемо у пам'ять (корисно для наступних відповідей)
        remember(chat_id, "user", text)
        remember(chat_id, "assistant", ABOUT_TEXT)
        return

    # Збираємо повідомлення з урахуванням історії
    messages = build_messages(chat_id, text)

    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.4
        )
        reply = completion["choices"][0]["message"]["content"].strip()
        await update.message.reply_text(reply)
        usage = completion.get("usage", {})
        logging.info("Chat OK | model=%s | prompt=%s | completion=%s",
                     completion.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens"))

        # Оновлюємо пам'ять: додаємо останній хід
        remember(chat_id, "user", text)
        remember(chat_id, "assistant", reply)

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
    app.add_handler(CommandHandler("about", about_cmd))
    app.add_handler(CommandHandler("context", context_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("imgtest", imgtest_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
