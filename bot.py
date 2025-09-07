import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import openai

# 🔑 Ключі з Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)

# 🧠 СИСТЕМНИЙ ПРОМПТ: місія чату + стиль
SYSTEM_PROMPT = (
    "Ти — офіційний помічник українського чату Vibe-Coding. "
    "Мета спільноти: новий підхід до створення коду, де ШІ (LLM) пише код, а люди формулюють ідеї. "
    "У чаті ділимось досвідом, ідеями, кодом, допомагаємо одне одному. Україна! "
    "Ми формуємо щільне комʼюніті, яке використовує ШІ для скриптів, програм, відео, аудіо та інших продуктів. "
    "Вайбкодинг зараз для декого виглядає як експеримент, але ми віримо, що це новий стандарт. "
    "Є сайт спільноти: vibe-coding.com.ua — точка збору професійних вайбкодерів, проєктів і роботи. "
    "Твій стиль: дуже ввічливий, підтримуючий, доброзичливий, лаконічний і по суті. "
    "Дозволена легка іронія щодо типових 'болей' програмістів (без образ і токсичності). "
    "Комунікуй українською. Якщо користувач звертається іншою мовою — ввічливо запропонуй перейти на українську. "
    "Безпека і етика: не допускай мови ненависті, дискримінації, принижень за національністю/етнічністю/статтю тощо. "
    "Недопустимі заклики до насильства, виправдання агресії або пропаганда війни. "
    "Якщо запит порушує правила — ввічливо відмовляй і коротко пояснюй причину, пропонуй безпечну альтернативу. "
    "Завжди допомагай з інженерією підказок, прикладами коду, налагодженням, ідеями для проєктів."
)

# 🛡️ Дуже проста 'модерація' на боці бота (можна розширити)
BLOCK_LIST = [
    # без детальних переліків — лише загальні маркери, щоб не зберігати образливі слова у коді
    "заклик до насильства", "знищити народ", "геноцид", "ненавиджу націю"
]
def violates_rules(text: str) -> bool:
    tl = text.lower()
    return any(key in tl for key in BLOCK_LIST)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я GPT-бот Vibe-Coding. Пиши з тегом @vibecoding_bot або починай повідомлення зі слова «бот» 🙂"
    )

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_message = update.message.text.strip()

    # ✅ Тригер: тег або початок з "бот"
    mentioned = bool(update.message.entities and any(e.type == "mention" for e in update.message.entities))
    starts_with_word = user_message.lower().startswith("бот")

    if not (mentioned or starts_with_word):
        return

    # 🛡️ Перевірка простих порушень правил
    if violates_rules(user_message):
        await update.message.reply_text(
            "Я за безпечну та поважну комунікацію. У чаті заборонена мова ненависті, "
            "дискримінація та заклики до насильства. Сформулюй, будь ласка, по-іншому 🙏"
        )
        return

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
    except Exception as e:
        logging.exception("OpenAI error: %s", e)
        await update.message.reply_text(
            "Вибач, тимчасова помилка відповіді ШІ. Спробуй ще раз за хвилинку 🙏"
        )

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.run_polling()

if __name__ == "__main__":
    main()
