import os
import json
import logging
import time
import asyncio
from datetime import datetime
from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
DB_FILE = "db.json"

# ── Rate limiting ──────────────────────────────────────────────
RATE_LIMIT_SECONDS = 2
MAX_INPUT_LENGTH = 500
_rate_cache: dict[int, float] = {}

def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last = _rate_cache.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    _rate_cache[user_id] = now
    return False

# ── States ─────────────────────────────────────────────────────
(ASKING_CLASS, ASKING_HOBBY1, ASKING_HOBBY2, ASKING_HOBBY3,
 ASKING_REGION, ASKING_BUDGET, ASKING_TEST, FREE_CHAT) = range(8)

(ADMIN_PASSWORD_INPUT, ADMIN_MENU, ADMIN_BROADCAST, ADMIN_BROADCAST_CONFIRM,
 ADMIN_BAN, ADMIN_CHAT, ADMIN_PROMPT_EDIT, ADMIN_USER_INFO,
 ADMIN_MESSAGE_USER) = range(8, 17)

# ── Вопросы-фолбэк ─────────────────────────────────────────────
QUESTIONS_JUNIOR = [
    "Что тебе нравится делать на уроках?\n\nА) Рассказывать, объяснять другим\nБ) Решать задачи и примеры\nВ) Проводить опыты, наблюдать\nГ) Рисовать, писать сочинения",
    "Если тебе дали свободный урок — ты:\n\nА) Болтаешь с одноклассниками\nБ) Играешь в игры на телефоне или думаешь над задачкой\nВ) Идёшь на улицу или занимаешься спортом\nГ) Рисуешь, слушаешь музыку, читаешь",
    "Какое хобби тебе ближе?\n\nА) Волонтёрство, помощь людям\nБ) Программирование, робототехника, конструктор\nВ) Туризм, природа, животные\nГ) Творчество — музыка, рисование, театр",
    "Что тебя больше раздражает в школе?\n\nА) Когда никто не слушает учителя\nБ) Когда не объясняют логику, просто говорят «запомни»\nВ) Сидеть в классе весь день без движения\nГ) Когда нет места для своих идей",
    "Твоя мечта после школы?\n\nА) Помогать людям, работать с детьми или больными\nБ) Создать крутую программу или устройство\nВ) Путешествовать и изучать природу\nГ) Стать известным артистом, дизайнером или писателем",
    "Что ты делаешь когда нужно принять решение?\n\nА) Спрашиваю совета у друзей или родителей\nБ) Анализирую все варианты логически\nВ) Доверяю интуиции и своему опыту\nГ) Слушаю своё чутьё и настроение",
    "Какой предмет даётся легче всего?\n\nА) История, литература, обществознание\nБ) Математика, физика, информатика\nВ) Биология, химия, география\nГ) Русский язык, ИЗО, музыка",
    "Если бы ты мог выбрать работу прямо сейчас?\n\nА) Работать с людьми — учить, лечить, помогать\nБ) Программировать, чинить технику, изобретать\nВ) Работать на природе, с животными или в лаборатории\nГ) Создавать — снимать, рисовать, писать",
]

QUESTIONS_SENIOR = [
    "Что тебе больше нравится в учёбе?\n\nА) Работать с людьми — проекты, дискуссии, командная работа\nБ) Решать сложные задачи — математика, физика, алгоритмы\nВ) Исследовать и экспериментировать — химия, биология, экология\nГ) Создавать — писать тексты, рисовать, снимать видео",
    "Как ты проводишь свободное время?\n\nА) Общаюсь, организую мероприятия, помогаю другим\nБ) Программирую, играю в стратегии, разбираю устройства\nВ) Провожу время на природе, занимаюсь спортом\nГ) Создаю что-то — музыка, арт, блог, видео",
    "Каким видишь себя через 10 лет?\n\nА) Работаю с людьми — управляю командой, помогаю, учу\nБ) Создаю технологии или решаю сложные технические задачи\nВ) Занимаюсь наукой или работаю на свежем воздухе\nГ) Занимаюсь творчеством или медиа",
    "Что важнее в будущей работе?\n\nА) Общение, влияние на людей, помощь\nБ) Логика, точность, интересные задачи\nВ) Свобода действий, исследования, природа\nГ) Самовыражение, творчество, признание",
    "Как принимаешь сложные решения?\n\nА) Советуюсь, учитываю мнения других\nБ) Анализирую данные и факты\nВ) Доверяю опыту и интуиции\nГ) Слушаю себя и своё ощущение",
    "Какой тип задач нравится?\n\nА) Организовывать людей и процессы\nБ) Решать технические и аналитические задачи\nВ) Исследовать, экспериментировать, находить закономерности\nГ) Придумывать и создавать новое",
    "Что тебя больше всего раздражает?\n\nА) Конфликты и недопонимание между людьми\nБ) Когда что-то работает неправильно и непонятно почему\nВ) Сидеть в офисе весь день без движения\nГ) Когда нет места для инициативы и творчества",
    "Что ты готов делать ради интересной работы?\n\nА) Много общаться и работать с разными людьми\nБ) Постоянно учиться новым технологиям\nВ) Работать в нестандартных условиях — в поле, в лаборатории\nГ) Мириться с нестабильностью ради любимого дела",
]

HOBBY_QUESTIONS = [
    "Чем занимаешься помимо учёбы? Можешь написать несколько вещей — хобби, секции, увлечения.",
    "Что тебе даётся легко, за что тебя хвалят? (учёба, спорт, творчество, техника — что угодно)",
    "Есть ли профессии которые тебя привлекают или наоборот пугают? Напиши честно.",
]

DEFAULT_SYSTEM_PROMPT = """Ты крутой профориентационный эксперт для школьников. Отвечай ТОЛЬКО на русском языке.

Стиль: живой и прямой, как умный старший друг. Без занудства, штампов и канцелярита.
Длина: коротко — 3-4 предложения максимум на один вопрос.
Честность: если профессия не подходит — говори прямо и объясни почему, не ври.
Тема: только профессии, образование, карьера, выбор пути. На остальное вежливо отказывай.
Учебные заведения: называй только реально существующие.
Зарплаты: указывай реальные средние по России за 2024 год."""


# ══════════════════════════════════════════════════════════════════
# БД — простой кеш в памяти + запись на диск
# ══════════════════════════════════════════════════════════════════

_db_cache: dict | None = None
_db_dirty = False


def load_db() -> dict:
    global _db_cache
    if _db_cache is not None:
        return _db_cache
    if not os.path.exists(DB_FILE):
        _db_cache = {
            "users": {},
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "profession_stats": {},
            "total_messages": 0,
            "admin_ids": [],
        }
        return _db_cache
    with open(DB_FILE, "r", encoding="utf-8") as f:
        _db_cache = json.load(f)
    return _db_cache


def save_db(db: dict) -> None:
    global _db_cache, _db_dirty
    _db_cache = db
    _db_dirty = True
    # Пишем сразу — для безопасности при перезапуске
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    _db_dirty = False


def is_admin(user_id: int) -> bool:
    return user_id in load_db().get("admin_ids", [])


def add_admin(user_id: int) -> None:
    db = load_db()
    if user_id not in db["admin_ids"]:
        db["admin_ids"].append(user_id)
    save_db(db)


def register_user(user_id: int, username: str | None, first_name: str | None) -> None:
    db = load_db()
    uid = str(user_id)
    now = datetime.now().isoformat()
    if uid not in db["users"]:
        db["users"][uid] = {
            "username": username or "",
            "first_name": first_name or "",
            "joined": now,
            "tests_completed": 0,
            "messages_sent": 0,
            "banned": False,
            "last_active": now,
            "grade": "",
            "region": "",
            "last_result": "",
            "feedback": [],
        }
    else:
        db["users"][uid]["last_active"] = now
        db["users"][uid]["username"] = username or ""
        db["users"][uid]["first_name"] = first_name or ""
    save_db(db)


def is_banned(user_id: int) -> bool:
    return load_db()["users"].get(str(user_id), {}).get("banned", False)


def save_last_result(user_id: int, result: str) -> None:
    db = load_db()
    uid = str(user_id)
    if uid in db["users"]:
        db["users"][uid]["last_result"] = result
    save_db(db)


def get_last_result(user_id: int) -> str:
    return load_db()["users"].get(str(user_id), {}).get("last_result", "")


def save_feedback(user_id: int, value: str) -> None:
    db = load_db()
    uid = str(user_id)
    if uid in db["users"]:
        db["users"][uid].setdefault("feedback", []).append({
            "value": value,
            "date": datetime.now().isoformat()[:10],
        })
    save_db(db)


def increment_tests(user_id: int, grade: str = "", region: str = "") -> None:
    db = load_db()
    uid = str(user_id)
    if uid in db["users"]:
        db["users"][uid]["tests_completed"] = db["users"][uid].get("tests_completed", 0) + 1
        if grade:
            db["users"][uid]["grade"] = grade
        if region:
            db["users"][uid]["region"] = region
    save_db(db)


def increment_messages(user_id: int) -> None:
    db = load_db()
    uid = str(user_id)
    if uid in db["users"]:
        db["users"][uid]["messages_sent"] = db["users"][uid].get("messages_sent", 0) + 1
    db["total_messages"] = db.get("total_messages", 0) + 1
    save_db(db)


def add_profession_stat(profession: str) -> None:
    db = load_db()
    db["profession_stats"][profession] = db["profession_stats"].get(profession, 0) + 1
    save_db(db)


def get_system_prompt() -> str:
    return load_db().get("system_prompt", DEFAULT_SYSTEM_PROMPT)


def set_system_prompt(prompt: str) -> None:
    db = load_db()
    db["system_prompt"] = prompt
    save_db(db)


def ban_user(identifier: str, ban: bool = True) -> str | None:
    db = load_db()
    identifier = identifier.lstrip("@")
    for uid, u in db["users"].items():
        if u.get("username") == identifier or uid == identifier:
            db["users"][uid]["banned"] = ban
            save_db(db)
            return u.get("username") or uid
    return None


def get_user_info(identifier: str) -> tuple[str | None, dict | None]:
    db = load_db()
    identifier = identifier.lstrip("@")
    for uid, u in db["users"].items():
        if u.get("username") == identifier or uid == identifier:
            return uid, u
    return None, None


def export_users_text() -> str:
    db = load_db()
    lines = ["ID | Username | Имя | Класс | Регион | Тестов | Сообщений | Забанен | Регистрация"]
    for uid, u in sorted(db["users"].items(), key=lambda x: x[1].get("joined", ""), reverse=True):
        status = "Да" if u.get("banned") else "Нет"
        lines.append(
            f"{uid} | @{u.get('username','—')} | {u.get('first_name','—')} | "
            f"{u.get('grade','?')} | {u.get('region','?')} | "
            f"{u.get('tests_completed',0)} | {u.get('messages_sent',0)} | "
            f"{status} | {u.get('joined','?')[:10]}"
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# AI — с retry и индикатором "печатает"
# ══════════════════════════════════════════════════════════════════

def ai_request(messages: list, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            response = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": "mistral-large-latest", "messages": messages},
                timeout=60,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"AI attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise RuntimeError("AI недоступен после нескольких попыток")


async def ai_request_with_typing(
    update: Update, messages: list, retries: int = 3
) -> str:
    """Запускает ai_request в фоне и шлёт ChatAction.TYPING пока ждём."""
    loop = asyncio.get_event_loop()
    chat_id = update.effective_chat.id

    async def keep_typing():
        while True:
            try:
                await update.effective_chat.send_action(ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        result = await loop.run_in_executor(None, lambda: ai_request(messages, retries))
    finally:
        typing_task.cancel()
    return result


# ══════════════════════════════════════════════════════════════════
# ОБЫЧНЫЙ БОТ
# ══════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name)

    if is_banned(user.id):
        await update.message.reply_text("Ты заблокирован.")
        return ConversationHandler.END

    context.user_data.clear()

    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Помогу разобраться с выбором профессии — честно и без воды.\n\n"
        "В каком ты классе?",
        reply_markup=ReplyKeyboardMarkup(
            [["8 класс", "9 класс"], ["10 класс", "11 класс"]],
            resize_keyboard=True, one_time_keyboard=True,
        ),
    )
    return ASKING_CLASS


async def asking_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grade = update.message.text.strip()
    context.user_data["grade"] = grade
    context.user_data["is_senior"] = int(grade.split()[0]) >= 10

    await update.message.reply_text(HOBBY_QUESTIONS[0], reply_markup=ReplyKeyboardRemove())
    return ASKING_HOBBY1


async def asking_hobby1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()[:MAX_INPUT_LENGTH]
    context.user_data["hobby1"] = text
    await update.message.reply_text(HOBBY_QUESTIONS[1])
    return ASKING_HOBBY2


async def asking_hobby2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()[:MAX_INPUT_LENGTH]
    context.user_data["hobby2"] = text
    await update.message.reply_text(HOBBY_QUESTIONS[2])
    return ASKING_HOBBY3


async def asking_hobby3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()[:MAX_INPUT_LENGTH]
    context.user_data["hobby3"] = text
    await update.message.reply_text(
        "Из какого ты города или региона?\n\nПодберу учебные заведения рядом."
    )
    return ASKING_REGION


async def asking_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["region"] = update.message.text.strip()[:100]
    await update.message.reply_text(
        "Рассматриваешь платное обучение?",
        reply_markup=ReplyKeyboardMarkup(
            [["Только бюджет", "Готов платить"], ["Рассмотрю оба варианта"]],
            resize_keyboard=True, one_time_keyboard=True,
        ),
    )
    return ASKING_BUDGET


async def asking_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["budget"] = update.message.text.strip()
    context.user_data["answers"] = []
    context.user_data["question_index"] = 0

    grade = context.user_data.get("grade", "")
    hobby1 = context.user_data.get("hobby1", "")
    hobby2 = context.user_data.get("hobby2", "")
    hobby3 = context.user_data.get("hobby3", "")
    is_senior = context.user_data.get("is_senior", False)

    await update.message.reply_text(
        "Генерирую персональный тест под тебя... ⚡",
        reply_markup=ReplyKeyboardRemove(),
    )

    try:
        prompt = f"""Создай 8 вопросов для теста профориентации школьника.

Данные о человеке:
- Класс: {grade}
- Хобби и увлечения: {hobby1}
- Сильные стороны: {hobby2}
- Интерес к профессиям: {hobby3}

Правила:
- Вопросы должны учитывать конкретные хобби и интересы этого человека
- Каждый вопрос — 4 варианта ответа (А, Б, В, Г)
- Вопросы должны помочь определить подходящую профессию
- Пиши на русском, коротко и понятно
- Вопросы должны быть разными — про предпочтения, стиль работы, ценности, мечты

Формат ответа — СТРОГО JSON массив, без лишнего текста:
[
  {{
    "q": "Текст вопроса",
    "a": "Вариант А",
    "b": "Вариант Б",
    "c": "Вариант В",
    "d": "Вариант Г"
  }}
]

Верни ТОЛЬКО JSON, ничего больше."""

        raw = await ai_request_with_typing(
            update, [{"role": "user", "content": prompt}]
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        questions = [
            f"{item['q']}\n\nА) {item['a']}\nБ) {item['b']}\nВ) {item['c']}\nГ) {item['d']}"
            for item in data[:8]
        ]
        context.user_data["generated_questions"] = questions

    except Exception as e:
        logger.error(f"Question gen error: {e}")
        context.user_data["generated_questions"] = (
            QUESTIONS_SENIOR if is_senior else QUESTIONS_JUNIOR
        )

    questions = context.user_data["generated_questions"]
    total = len(questions)
    markup = ReplyKeyboardMarkup([["А", "Б"], ["В", "Г"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Твой персональный тест готов! Отвечай честно 🎯")
    await update.message.reply_text(f"Вопрос 1/{total}\n\n{questions[0]}", reply_markup=markup)
    return ASKING_TEST


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_rate_limited(update.effective_user.id):
        return ASKING_TEST

    text = update.message.text.strip().upper()
    if text not in ["А", "Б", "В", "Г"]:
        await update.message.reply_text("Жми А, Б, В или Г 👇")
        return ASKING_TEST

    context.user_data["answers"].append(text)
    index = context.user_data["question_index"] + 1
    context.user_data["question_index"] = index

    questions = context.user_data.get("generated_questions") or (
        QUESTIONS_SENIOR if context.user_data.get("is_senior") else QUESTIONS_JUNIOR
    )
    total = len(questions)

    if index < total:
        markup = ReplyKeyboardMarkup([["А", "Б"], ["В", "Г"]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            f"Вопрос {index + 1}/{total}\n\n{questions[index]}",
            reply_markup=markup,
        )
        return ASKING_TEST
    else:
        await update.message.reply_text("Готово, анализирую... ⚡", reply_markup=ReplyKeyboardRemove())
        await analyze_and_respond(update, context)
        return FREE_CHAT


async def analyze_and_respond(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answers = context.user_data["answers"]
    grade = context.user_data.get("grade", "")
    region = context.user_data.get("region", "")
    budget = context.user_data.get("budget", "")
    hobby1 = context.user_data.get("hobby1", "")
    hobby2 = context.user_data.get("hobby2", "")
    hobby3 = context.user_data.get("hobby3", "")
    is_senior = context.user_data.get("is_senior", False)

    questions = context.user_data.get("generated_questions") or (
        QUESTIONS_SENIOR if is_senior else QUESTIONS_JUNIOR
    )
    pairs = [f"Вопрос {i+1}: {q}\nОтвет: {a}" for i, (q, a) in enumerate(zip(questions, answers))]
    answers_text = "\n\n".join(pairs)

    grade_context = (
        "Ученик 10-11 класса — скоро ЕГЭ, выбор вуза актуален прямо сейчас."
        if is_senior else
        "Ученик 8-9 класса — впереди ОГЭ и выбор профиля. Можно рассматривать и колледж после 9го."
    )

    prompt = f"""Ты профориентационный эксперт — умный старший друг. Отвечай ТОЛЬКО на русском, никаких иностранных символов.

{grade_context}

Данные:
- Класс: {grade}
- Регион: {region}
- Бюджет: {budget}
- Хобби и увлечения: {hobby1}
- Сильные стороны: {hobby2}
- Привлекающие/пугающие профессии: {hobby3}

Ответы на тест:
{answers_text}

Напиши коротко и живо, строго по структуре:

🧠 Профиль
1-2 предложения — кто ты по характеру и что тебя драйвит.

💼 Топ-3 профессии
Каждая одной строкой: Название → почему подходит → зарплата → ЕГЭ/ОГЭ

🎓 Где учиться в {region}
По 1 заведению на профессию. Только реальные. Бюджет: {budget}.

💪 Козыри: 3 качества одной строкой

🚀 Один шаг прямо сейчас

Пиши на "ты", максимально коротко и по делу."""

    try:
        result = await ai_request_with_typing(update, [{"role": "user", "content": prompt}])
        await update.message.reply_text(result)

        # Кнопки обратной связи
        feedback_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👍 Точно в цель", callback_data="fb_good"),
                InlineKeyboardButton("👎 Мимо", callback_data="fb_bad"),
            ]
        ])
        await update.message.reply_text(
            "Результат оказался полезным?",
            reply_markup=feedback_markup,
        )
        await update.message.reply_text(
            "Спрашивай что угодно про профессии — отвечу честно 💬\n\n"
            "/results — посмотреть этот результат снова\n"
            "/start — пройти тест заново"
        )

        context.user_data["profile_summary"] = result
        context.user_data["chat_history"] = []
        save_last_result(update.effective_user.id, result)
        increment_tests(update.effective_user.id, grade, region)

        for line in result.split("\n"):
            line = line.strip()
            if "→" in line:
                prof = line.split("→")[0].lstrip("•123. ").strip()
                if 3 < len(prof) < 40:
                    add_profession_stat(prof)

    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("Что-то сломалось. Попробуй /start заново.")


async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "fb_good":
        save_feedback(query.from_user.id, "good")
        await query.edit_message_text("Отлично! Рад помочь 💪")
    elif query.data == "fb_bad":
        save_feedback(query.from_user.id, "bad")
        await query.edit_message_text(
            "Жаль, что не попал 😕 Попробуй /start — можешь указать другие интересы."
        )


async def results_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = get_last_result(update.effective_user.id)
    if not result:
        await update.message.reply_text(
            "У тебя ещё нет сохранённых результатов. Пройди тест — /start"
        )
        return
    await update.message.reply_text(f"📋 Твой последний результат:\n\n{result}")


async def free_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if is_banned(user_id):
        await update.message.reply_text("Ты заблокирован.")
        return FREE_CHAT

    if is_rate_limited(user_id):
        return FREE_CHAT

    if not context.user_data.get("profile_summary"):
        await update.message.reply_text("Напиши /start чтобы начать.")
        return FREE_CHAT

    question = update.message.text.strip()
    if len(question) > MAX_INPUT_LENGTH:
        await update.message.reply_text(
            f"Слишком длинное сообщение. Напиши покороче (до {MAX_INPUT_LENGTH} символов)."
        )
        return FREE_CHAT

    profile = context.user_data.get("profile_summary", "")
    region = context.user_data.get("region", "")
    budget = context.user_data.get("budget", "")
    grade = context.user_data.get("grade", "")
    is_senior = context.user_data.get("is_senior", False)
    history = context.user_data.get("chat_history", [])

    grade_context = (
        "Ученик 10-11 класса, ЕГЭ актуален."
        if is_senior else
        "Ученик 8-9 класса, ОГЭ и выбор профиля."
    )

    system = (
        f"{get_system_prompt()}\n\n"
        f"{grade_context}\n"
        f"Профиль по результатам теста:\n{profile}\n\n"
        f"Регион: {region} | Бюджет: {budget} | Класс: {grade}"
    )

    messages = [{"role": "system", "content": system}]
    messages += history[-8:]
    messages.append({"role": "user", "content": question})

    try:
        result = await ai_request_with_typing(update, messages)
        await update.message.reply_text(result)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": result})
        context.user_data["chat_history"] = history[-20:]
        increment_messages(user_id)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("Ошибка. Попробуй ещё раз.")

    return FREE_CHAT


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Что умею:\n\n"
        "• Тест профориентации (разный для 8-9 и 10-11 класса)\n"
        "• Анализ с учётом твоих хобби и сильных сторон\n"
        "• Топ-3 профессии с зарплатой и ЕГЭ/ОГЭ\n"
        "• Подборка вузов и колледжей в твоём регионе\n"
        "• Честные ответы на вопросы про профессии\n\n"
        "/start — начать тест\n"
        "/results — последний результат\n"
        "/help — это сообщение"
    )


# ══════════════════════════════════════════════════════════════════
# АДМИНИСТРАТОРСКАЯ ПАНЕЛЬ
# ══════════════════════════════════════════════════════════════════

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 Статистика", "👥 Пользователи"],
        ["📢 Рассылка", "🔍 Найти юзера"],
        ["🚫 Забанить", "✅ Разбанить"],
        ["✉️ Написать юзеру", "📤 Экспорт"],
        ["💬 Чат с ИИ", "✏️ Промпт"],
        ["❌ Выйти"],
    ],
    resize_keyboard=True,
)


async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await show_admin_stats(update)
        return ADMIN_MENU

    await update.message.reply_text("🔐 Пароль:", reply_markup=ReplyKeyboardRemove())
    return ADMIN_PASSWORD_INPUT


async def admin_check_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == ADMIN_PASSWORD:
        add_admin(update.effective_user.id)
        await update.message.reply_text(
            "Доступ получен. Твой аккаунт сохранён — больше пароль не нужен. ✅"
        )
        await show_admin_stats(update)
        return ADMIN_MENU
    await update.message.reply_text("Неверный пароль.")
    return ConversationHandler.END


async def show_admin_stats(update: Update):
    db = load_db()
    users = db["users"]
    total = len(users)
    banned = sum(1 for u in users.values() if u.get("banned"))
    tests = sum(u.get("tests_completed", 0) for u in users.values())
    messages_count = db.get("total_messages", 0)

    today = datetime.now().date().isoformat()
    active_today = sum(1 for u in users.values() if u.get("last_active", "")[:10] == today)

    top_profs = sorted(
        db.get("profession_stats", {}).items(), key=lambda x: x[1], reverse=True
    )[:5]
    top_text = "\n".join(
        [f"  {i+1}. {p} — {c}" for i, (p, c) in enumerate(top_profs)]
    ) or "  нет данных"

    top_regions: dict[str, int] = {}
    for u in users.values():
        r = u.get("region", "")
        if r:
            top_regions[r] = top_regions.get(r, 0) + 1
    top_reg = sorted(top_regions.items(), key=lambda x: x[1], reverse=True)[:3]
    reg_text = ", ".join([f"{r} ({c})" for r, c in top_reg]) or "нет данных"

    seniors = sum(
        1 for u in users.values() if "10" in u.get("grade", "") or "11" in u.get("grade", "")
    )

    # Статистика обратной связи
    good_fb = sum(
        1 for u in users.values()
        for f in u.get("feedback", []) if f.get("value") == "good"
    )
    bad_fb = sum(
        1 for u in users.values()
        for f in u.get("feedback", []) if f.get("value") == "bad"
    )
    fb_text = f"👍 {good_fb}  👎 {bad_fb}" if (good_fb + bad_fb) else "нет данных"

    await update.message.reply_text(
        f"👑 Панель администратора\n\n"
        f"👥 Пользователей: {total}\n"
        f"🟢 Активны сегодня: {active_today}\n"
        f"🚫 Забанено: {banned}\n"
        f"📝 Тестов пройдено: {tests}\n"
        f"💬 Сообщений всего: {messages_count}\n"
        f"🎓 8-9 класс: {total - seniors} | 10-11 класс: {seniors}\n"
        f"⭐ Обратная связь: {fb_text}\n\n"
        f"🏆 Топ профессий:\n{top_text}\n\n"
        f"📍 Топ регионов: {reg_text}",
        reply_markup=ADMIN_KEYBOARD,
    )


async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ Выйти":
        await update.message.reply_text("Вышел.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    elif text == "📊 Статистика":
        await show_admin_stats(update)
        return ADMIN_MENU

    elif text == "👥 Пользователи":
        db = load_db()
        users = db["users"]
        if not users:
            await update.message.reply_text("Пользователей нет.", reply_markup=ADMIN_KEYBOARD)
        else:
            lines = []
            for uid, u in sorted(
                users.items(), key=lambda x: x[1].get("last_active", ""), reverse=True
            )[:15]:
                status = "🚫" if u.get("banned") else "🟢"
                name = f"@{u['username']}" if u.get("username") else u.get("first_name", uid)
                lines.append(
                    f"{status} {name} | {u.get('grade','?')} | "
                    f"{u.get('region','?')} | тестов: {u.get('tests_completed',0)}"
                )
            await update.message.reply_text(
                "Последние 15:\n\n" + "\n".join(lines), reply_markup=ADMIN_KEYBOARD
            )
        return ADMIN_MENU

    elif text == "📢 Рассылка":
        await update.message.reply_text("Текст рассылки:", reply_markup=ReplyKeyboardRemove())
        return ADMIN_BROADCAST

    elif text == "🔍 Найти юзера":
        await update.message.reply_text("Username или ID:", reply_markup=ReplyKeyboardRemove())
        return ADMIN_USER_INFO

    elif text == "🚫 Забанить":
        context.user_data["ban_action"] = "ban"
        await update.message.reply_text("Username или ID для бана:", reply_markup=ReplyKeyboardRemove())
        return ADMIN_BAN

    elif text == "✅ Разбанить":
        context.user_data["ban_action"] = "unban"
        await update.message.reply_text("Username или ID для разбана:", reply_markup=ReplyKeyboardRemove())
        return ADMIN_BAN

    elif text == "✉️ Написать юзеру":
        await update.message.reply_text(
            "Введи: @username или ID и через пробел текст сообщения\n"
            "Пример: @vasya Привет, твой аккаунт проверен.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADMIN_MESSAGE_USER

    elif text == "📤 Экспорт":
        content = export_users_text()
        # Шлём как файл если длинный
        if len(content) > 3000:
            import io
            bio = io.BytesIO(content.encode("utf-8"))
            bio.name = "users_export.txt"
            await update.message.reply_document(bio, caption="Экспорт пользователей", reply_markup=ADMIN_KEYBOARD)
        else:
            await update.message.reply_text(f"```\n{content}\n```", parse_mode="Markdown", reply_markup=ADMIN_KEYBOARD)
        return ADMIN_MENU

    elif text == "💬 Чат с ИИ":
        context.user_data["admin_chat_history"] = []
        await update.message.reply_text(
            "Чат с ИИ без ограничений. /adminmenu — назад.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADMIN_CHAT

    elif text == "✏️ Промпт":
        current = get_system_prompt()
        await update.message.reply_text(
            f"Текущий промпт:\n\n{current}\n\nНапиши новый:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADMIN_PROMPT_EDIT

    return ADMIN_MENU


async def admin_broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["broadcast_text"] = text
    db = load_db()
    active = sum(1 for u in db["users"].values() if not u.get("banned"))
    await update.message.reply_text(
        f"Предпросмотр:\n\n📢 {text}\n\nОтправить {active} пользователям?",
        reply_markup=ReplyKeyboardMarkup(
            [["✅ Отправить", "❌ Отмена"]], resize_keyboard=True, one_time_keyboard=True
        ),
    )
    return ADMIN_BROADCAST_CONFIRM


async def admin_broadcast_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, app: Application
):
    if update.message.text.strip() != "✅ Отправить":
        await update.message.reply_text("Рассылка отменена.", reply_markup=ADMIN_KEYBOARD)
        return ADMIN_MENU

    text = context.user_data.get("broadcast_text", "")
    db = load_db()
    sent = failed = 0

    await update.message.reply_text("Отправляю...", reply_markup=ReplyKeyboardRemove())

    for uid, u in db["users"].items():
        if u.get("banned"):
            continue
        try:
            await app.bot.send_message(chat_id=int(uid), text=f"📢 {text}")
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"Готово. Отправлено: {sent}, не дошло: {failed}",
        reply_markup=ADMIN_KEYBOARD,
    )
    return ADMIN_MENU


async def admin_ban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    action = context.user_data.get("ban_action", "ban")
    result = ban_user(target, ban=(action == "ban"))

    if result:
        word = "забанен 🚫" if action == "ban" else "разбанен ✅"
        await update.message.reply_text(f"{result} {word}.", reply_markup=ADMIN_KEYBOARD)
    else:
        await update.message.reply_text("Не найден.", reply_markup=ADMIN_KEYBOARD)

    return ADMIN_MENU


async def admin_user_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    uid, u = get_user_info(target)

    if not u:
        await update.message.reply_text("Не найден.", reply_markup=ADMIN_KEYBOARD)
        return ADMIN_MENU

    status = "🚫 Забанен" if u.get("banned") else "🟢 Активен"
    name = f"@{u['username']}" if u.get("username") else u.get("first_name", "?")
    good = sum(1 for f in u.get("feedback", []) if f.get("value") == "good")
    bad = sum(1 for f in u.get("feedback", []) if f.get("value") == "bad")

    await update.message.reply_text(
        f"👤 {name} (ID: {uid})\n"
        f"Статус: {status}\n"
        f"Класс: {u.get('grade', '?')}\n"
        f"Регион: {u.get('region', '?')}\n"
        f"Тестов: {u.get('tests_completed', 0)}\n"
        f"Сообщений: {u.get('messages_sent', 0)}\n"
        f"Обратная связь: 👍 {good}  👎 {bad}\n"
        f"Зарегистрирован: {u.get('joined', '?')[:10]}\n"
        f"Последняя активность: {u.get('last_active', '?')[:10]}",
        reply_markup=ADMIN_KEYBOARD,
    )
    return ADMIN_MENU


async def admin_message_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, app: Application):
    """Формат: @username текст  или  123456789 текст"""
    raw = update.message.text.strip()
    parts = raw.split(None, 1)

    if len(parts) < 2:
        await update.message.reply_text(
            "Неверный формат. Пример: @vasya Привет!",
            reply_markup=ADMIN_KEYBOARD,
        )
        return ADMIN_MENU

    identifier, message_text = parts[0], parts[1]
    uid, u = get_user_info(identifier)

    if not uid:
        await update.message.reply_text("Пользователь не найден.", reply_markup=ADMIN_KEYBOARD)
        return ADMIN_MENU

    try:
        await app.bot.send_message(
            chat_id=int(uid),
            text=f"✉️ Сообщение от администратора:\n\n{message_text}",
        )
        name = f"@{u.get('username')}" if u.get("username") else uid
        await update.message.reply_text(f"Отправлено {name} ✅", reply_markup=ADMIN_KEYBOARD)
    except Exception as e:
        logger.error(f"DM error: {e}")
        await update.message.reply_text(f"Не удалось отправить: {e}", reply_markup=ADMIN_KEYBOARD)

    return ADMIN_MENU


async def admin_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "/adminmenu":
        await show_admin_stats(update)
        return ADMIN_MENU

    history: list = context.user_data.get("admin_chat_history", [])
    history.append({"role": "user", "content": text})

    try:
        result = await ai_request_with_typing(update, history)
        await update.message.reply_text(result)
        history.append({"role": "assistant", "content": result})
        context.user_data["admin_chat_history"] = history[-20:]
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("Ошибка.")

    return ADMIN_CHAT


async def admin_prompt_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_system_prompt(update.message.text.strip())
    await update.message.reply_text("Промпт обновлён ✅", reply_markup=ADMIN_KEYBOARD)
    return ADMIN_MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Замыкания для передачи app в хендлеры
    async def broadcast_confirm_wrapper(u, c):
        return await admin_broadcast_confirm(u, c, app)

    async def message_user_wrapper(u, c):
        return await admin_message_user_handler(u, c, app)

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_start)],
        states={
            ADMIN_PASSWORD_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_password)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_menu_handler)],
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_handler)],
            ADMIN_BROADCAST_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_confirm_wrapper)],
            ADMIN_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_handler)],
            ADMIN_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_chat_handler)],
            ADMIN_PROMPT_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_prompt_edit_handler)],
            ADMIN_USER_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_user_info_handler)],
            ADMIN_MESSAGE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, message_user_wrapper)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    user_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASKING_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, asking_class)],
            ASKING_HOBBY1: [MessageHandler(filters.TEXT & ~filters.COMMAND, asking_hobby1)],
            ASKING_HOBBY2: [MessageHandler(filters.TEXT & ~filters.COMMAND, asking_hobby2)],
            ASKING_HOBBY3: [MessageHandler(filters.TEXT & ~filters.COMMAND, asking_hobby3)],
            ASKING_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, asking_region)],
            ASKING_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, asking_budget)],
            ASKING_TEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer)],
            FREE_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, free_chat)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(admin_conv)
    app.add_handler(user_conv)
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("results", results_command))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern="^fb_"))

    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
