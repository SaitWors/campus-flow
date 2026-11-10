CATEGORIES = {
    "contracts": ("Сайт, контракты и XP", "Website, contracts & XP"),
    "companion": ("Companion / Overwolf", "Companion / Overwolf"),
    "crash": ("Вылет или запуск игры", "Game crash or launch issue"),
    "ban": ("Бан или штраф в игре", "Game ban or matchmaking penalty"),
    "other": ("Другое", "Something else"),
}

FORUM = "https://forums.playdeadlock.com/forums/bug-reports.6/"
GAME_ISSUES = "https://help.steampowered.com/{language}/wizard/HelpWithGameTechnicalIssue/?appid=1422450"
GAME_SUPPORT = "https://help.steampowered.com/{language}/wizard/HelpWithGame/?appid=1422450"

TEXT = {
    "start": (
        "Поддержка Deadlock Contracts, фанатского проекта по Deadlock. Напиши вопрос или выбери тему из FAQ.",
        "Support for Deadlock Contracts, a fan project for Deadlock. Type a question or choose an FAQ topic.",
    ),
    "ticket_category": ("Выбери тему обращения.", "Choose a ticket category."),
    "new_ticket": ("Создать обращение", "Open a support ticket"),
    "faq_title": ("Частые вопросы", "Frequently asked questions"),
    "faq_search": ("Найти решение", "Find a solution"),
    "faq_search_hint": (
        "Напиши, что случилось. Например, «вылетает посреди матча», «не пришёл XP» или «истёк код привязки».",
        "Type what happened. For example, “crashes mid match”, “missing XP” or “pairing code expired”.",
    ),
    "faq_search_open": (
        "Для поиска при открытом обращении используй команду /faq с вопросом. Например, /faq не пришёл XP.",
        "Use /faq followed by a question while you have an open ticket. For example, /faq missing XP.",
    ),
    "faq_matches": ("Возможно, подходит одна из этих подсказок.", "These guides may match your question."),
    "faq_no_match": (
        "Подходящей подсказки не нашлось. Можно выбрать тему или создать обращение.",
        "No matching guide. You can browse the topics or open a support ticket.",
    ),
    "faq_contact": ("Не помогло · написать в поддержку", "Still need help? Contact support"),
    "faq_valve_contact": ("Написать в Deadlock Contracts", "Contact Deadlock Contracts"),
    "faq_answered": ("Помогло", "That helped"),
    "faq_back": ("К вопросам раздела", "Back to this topic"),
    "home": ("В главное меню", "Main menu"),
    "setup": (
        "Поддержка Deadlock Contracts ещё настраивается. Попробуй позже.",
        "Deadlock Contracts support is still being set up. Please try again later.",
    ),
    "crash_help": (
        "Если вылетает сама игра, начни с поддержки Steam или раздела Bug Reports на форуме Deadlock. "
        "Если вылет связан с Companion, можешь описать проблему нашей команде.",
        "If the game itself crashes, start with Steam Support or Bug Reports on the Deadlock forum. "
        "If Companion seems involved, you can send our team the details.",
    ),
    "ban_help": (
        "Блокировками и штрафами в Deadlock занимается Valve. Войди в Steam, чтобы посмотреть варианты "
        "поддержки для своего аккаунта. Deadlock Contracts не может снять игровую блокировку.",
        "Valve handles Deadlock bans and matchmaking penalties. Sign in to Steam to see the support options "
        "for your account. Deadlock Contracts cannot remove an in-game restriction.",
    ),
    "steam": ("Поддержка Steam", "Steam Support"),
    "forum": ("Форум Deadlock · Bug Reports", "Deadlock forum · Bug Reports"),
    "continue": ("Написать в Deadlock Contracts", "Contact Deadlock Contracts"),
    "back": ("К списку тем", "Back to categories"),
    "prompt": (
        "Опиши, что произошло, что ты ожидал и когда это случилось (дата, время и часовой пояс). "
        "Можно приложить скриншот. Обращение увидит владелец проекта. "
        "Пароли, коды Steam Guard, cookies и токены присылать не нужно.",
        "Describe what happened, what you expected, and when it happened (date, time, and time zone). "
        "You can attach a screenshot. The project owner will see your report. "
        "Keep passwords, Steam Guard codes, cookies and tokens private.",
    ),
    "contracts_hint": (
        "Если ошибка в зачёте контракта, добавь его название и ID матча, если он известен.",
        "If a contract did not count, include its name and the match ID if you know it.",
    ),
    "companion_hint": (
        "Добавь версию Companion и точный текст ошибки, если они известны.",
        "Include your Companion version and the exact error if available.",
    ),
    "open": (
        "Обращение #{ticket} открыто. Следующее сообщение добавится к нему.",
        "Ticket #{ticket} is open. Your next message will be added to it.",
    ),
    "created": ("Обращение #{ticket} принято.", "Ticket #{ticket} received."),
    "added": ("Сообщение добавлено к #{ticket}.", "Message added to ticket #{ticket}."),
    "closed": ("Обращение #{ticket} закрыто. Новое обращение можно создать через /new.",
               "Ticket #{ticket} is closed. Use /new to open another ticket."),
    "cancelled": ("Черновик отменён.", "Draft cancelled."),
    "empty": ("Открытых обращений нет. Создать обращение можно через /new.",
              "You have no open tickets. Use /new to open one."),
    "supported": ("Отправь текст или фото.", "Send a text message or a photo."),
    "help": ("/faq — частые вопросы\n/faq вопрос — найти решение\n/new — новое обращение\n"
             "/status — состояние обращения\n/cancel — отменить черновик\n/language — язык",
             "/faq — frequently asked questions\n/faq question — find a solution\n/new — new ticket\n"
             "/status — ticket status\n/cancel — cancel draft\n/language — language"),
    "language": ("Выбери язык.", "Choose a language."),
    "reply": ("Ответ по обращению #{ticket}\n\n", "Reply to ticket #{ticket}\n\n"),
}


def tr(key, language, **fields):
    return TEXT[key][language != "ru"].format(**fields)


def category_name(category, language):
    return CATEGORIES[category][language != "ru"]
