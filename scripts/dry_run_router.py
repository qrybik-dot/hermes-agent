import json

# 15 тестовых задач для Task Intake Router
tasks_routing = [
    {
        "id": "T1",
        "description": "Посмотреть текущий статус докер контейнеров на VPS"
    },
    {
        "id": "T2",
        "description": "Добавить новую переменную окружения DB_PASSWORD в защищенный файл .env и перезапустить службу hermes"
    },
    {
        "id": "T3",
        "description": "Оптимизировать производительность ядра run_agent.py, сократив потребление токенов при длинных сессиях"
    },
    {
        "id": "T4",
        "description": "Исправить опечатку в файле README.md"
    },
    {
        "id": "T5",
        "description": "Интегрировать новый API-провайдер для отправки SMS-уведомлений, требующий установки библиотеки twilio"
    },
    {
        "id": "T6",
        "description": "Создать еженедельный дайджест на основе выполненных задач из Kanban"
    },
    {
        "id": "T7",
        "description": "Удалить старые логи сессий старше 30 дней из SQLite базы данных hermes_state.py"
    },
    {
        "id": "T8",
        "description": "Найти в интернете документацию по новой библиотеке pydantic-v2 и обновить существующие схемы валидации в проекте"
    },
    {
        "id": "T9",
        "description": "Написать скрипт резервного копирования всей папки ~/.hermes и запускать его по крону каждую ночь"
    },
    {
        "id": "T10",
        "description": "Сделать рефакторинг всего модуля gateway/ с разделением на независимые микросервисы"
    },
    {
        "id": "T11",
        "description": "Проверить, свободен ли порт 8317 на localhost"
    },
    {
        "id": "T12",
        "description": "Полностью переустановить python-окружение и зависимости в venv"
    },
    {
        "id": "T13",
        "description": "Обнови requirements.txt и поставь пакет"
    },
    {
        "id": "T14",
        "description": "Сделай красиво"
    },
    {
        "id": "T15",
        "description": "Проверить systemd статус без изменений"
    }
]

# 9 тестовых задач для Graphic Generation Router
graphics_routing = [
    {
        "id": "G1",
        "description": "Нарисовать логотип для нового Telegram-бота Антона в стиле киберпанк"
    },
    {
        "id": "G2",
        "description": "Показать схему взаимодействия компонентов gateway, cli-proxy-api и run_agent.py"
    },
    {
        "id": "G3",
        "description": "Подготовить презентацию со слайдами о результатах работы бота за неделю"
    },
    {
        "id": "G4",
        "description": "Сделать реалистичную картинку робота-помощника, читающего бумажную книгу"
    },
    {
        "id": "G5",
        "description": "Создать интерактивный дашборд со статистикой вызовов API"
    },
    {
        "id": "G6",
        "description": "Сгенерировать абстрактный фон для слайда презентации в темных тонах"
    },
    {
        "id": "G7",
        "description": "Сделай баннер с русским текстом"
    },
    {
        "id": "G8",
        "description": "Отредактируй существующую картинку по референсу"
    },
    {
        "id": "G9",
        "description": "Сделай серию из 5 картинок с одним персонажем"
    }
]

def classify_task(task):
    desc = task["description"].lower()
    
    # Defaults
    size = "normal"
    domain = "code"
    ambiguity = "low"
    risk = "low"
    write_scope = "project"
    needs_web = False
    needs_context7 = False
    ask_policy = "ask_once"
    mode = "Step-by-Step"
    
    # Simple + read checks (T1, T11, T15)
    if any(x in desc for x in ["статус", "посмотреть", "проверить", "свободен", "порт"]) and "добавить" not in desc and "перезапустить" not in desc and "удалить" not in desc:
        size = "simple"
        domain = "system" if any(x in desc for x in ["порт", "докер", "systemd"]) else "code"
        risk = "low"
        write_scope = "none"
        ask_policy = "do_not_ask"
        mode = "Direct"
        
    # Simple + markdown docs edits (T4)
    elif any(x in desc for x in ["опечатку", "readme.md"]):
        size = "simple"
        domain = "content"
        risk = "low"
        write_scope = "project"
        ask_policy = "do_not_ask" # docs only
        mode = "Direct"
        
    # Risky system tasks (T2, T7, T9, T12)
    elif any(x in desc for x in [".env", "перезапустить", "systemd перезапустить", "удалить", "переустановить", "крон", "cron"]):
        size = "risky"
        domain = "system"
        risk = "high"
        write_scope = "vps"
        ask_policy = "ask_before_risky_step"
        mode = "Safe-Write & Rollback"
        
    # Large or highly ambiguous tasks (T3, T10, T14)
    elif any(x in desc for x in ["оптимизировать", "ядра", "рефакторинг", "сделай красиво"]):
        size = "large"
        domain = "code"
        ambiguity = "high"
        risk = "medium"
        write_scope = "project"
        ask_policy = "ask_before_write"
        mode = "Plan-First"
        
    # Dependency changes / Package installs (T5, T13)
    elif any(x in desc for x in ["twilio", "requirements.txt", "поставь пакет", "package.json"]):
        size = "normal"
        domain = "code"
        risk = "medium" # Dependency change is medium risk minimum!
        write_scope = "project"
        needs_context7 = True if "twilio" in desc else False
        needs_web = True if "twilio" in desc else False
        ask_policy = "ask_before_write"
        mode = "Step-by-Step"
        
    # Normal content/info search
    elif "дайджест" in desc:
        size = "normal"
        domain = "content"
        write_scope = "local"
        ask_policy = "ask_once"
        mode = "Step-by-Step"
    elif "pydantic-v2" in desc:
        size = "normal"
        domain = "code"
        needs_web = True
        needs_context7 = True
        ask_policy = "ask_before_write"
        mode = "Step-by-Step"
        
    return {
        "id": task["id"],
        "description": task["description"],
        "task_size": size,
        "task_domain": domain,
        "ambiguity": ambiguity,
        "risk": risk,
        "write_scope": write_scope,
        "needs_web": needs_web,
        "needs_context7": needs_context7,
        "ask_policy": ask_policy,
        "execution_mode": mode
    }

def classify_graphic(task):
    desc = task["description"].lower()
    
    tool = "gpt_image"
    reason = "Художественное изображение по текстовому описанию"
    
    if any(x in desc for x in ["схема", "взаимодействия", "диаграмма"]):
        tool = "mermaid"
        reason = "Диаграмма связей / логическая схема (точный текст/схема)"
    elif any(x in desc for x in ["презентация", "презентацию", "слайды"]):
        tool = "presentation_chain"
        reason = "Цепочка создания презентаций: research brief -> outline -> HTML/PPTX -> visual elements"
    elif any(x in desc for x in ["дашборд", "статистика"]):
        tool = "html_svg"
        reason = "Интерактивный UI-компонент / дашборд на HTML/SVG (точный текст/данные)"
    elif any(x in desc for x in ["фон", "абстрактный"]):
        tool = "gemini_image"
        reason = "Абстрактное изображение / иконка / текстура"
    elif "русским текстом" in desc or "баннер" in desc:
        tool = "html_svg"
        reason = "Баннер с точным текстом: Mermaid/SVG/HTML предпочтительнее для точного текста"
    elif any(x in desc for x in ["отредактируй", "референсу", "серию", "персонажем"]):
        tool = "gemini_image"
        reason = "Использование Nano Banana / Gemini Image для редактирования (editing), reference-based generation или style series"
        
    return {
        "id": task["id"],
        "description": task["description"],
        "target_tool": tool,
        "reason": reason
    }

# Run dry-run
task_results = [classify_task(t) for t in tasks_routing]
graphic_results = [classify_graphic(g) for g in graphics_routing]

# Save output to print/write
print("=== TASK INTAKE ROUTER DRY-RUN ===")
print(json.dumps(task_results, indent=2, ensure_ascii=False))

print("\n=== GRAPHIC GENERATION ROUTER DRY-RUN ===")
print(json.dumps(graphic_results, indent=2, ensure_ascii=False))
