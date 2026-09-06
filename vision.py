import os
import json
import base64
import asyncio
import requests
from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL_NAME", "openrouter/free")

API_URL = "https://openrouter.ai/api/v1/chat/completions"


# Изменения в файле vision.py

PROMPT = """
Ты распознаёшь фотографию кассового чека и классифицируешь каждый товар по категориям.

Верни ТОЛЬКО корректный JSON без markdown, без ``` и без дополнительных комментариев.

Формат:
{
  "store": "Название магазина",
  "total": 1234.56,
  "items": [
    {
      "name": "Название товара",
      "price": 123.45,
      "category": "Название категории"
    }
  ]
}

Допустимые категории для товаров (выбирай наиболее подходящую):
- Продукты (еда, напитки, супермаркеты)
- Коммуналка (бытовая химия, товары для дома, ремонт)
- Транспорт (бензин, автотовары, такси)
- Развлечения (кафе, фастфуд, билеты, алкоголь, табак)
- Прочее (аптека, одежда, цветы, электроника или всё то, что не подошло выше)

Правила:
1. store — название магазина с чека.
2. total — итоговая сумма чека как число.
3. items — список всех товаров, которые удалось распознать.
4. price — цена конкретного товара как число.
5. category — строго одна из пяти категорий, перечисленных выше.
6. Не добавляй валюту к числам.
7. Ответ должен быть только JSON.
"""


def _send_request(payload):
    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )

    return response


def _parse_json(text):
    """
    Пытаемся получить JSON даже если модель случайно
    добавила лишний текст или markdown.
    """

    text = text.strip()

    # Убираем markdown-обёртку
    text = text.replace("```json", "")
    text = text.replace("```", "")
    text = text.strip()

    # Первая попытка — весь ответ
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Вторая попытка — найти JSON между первой { и последней }
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Модель не вернула JSON")

    json_text = text[start:end + 1]

    return json.loads(json_text)


def _validate_result(data):
    if not isinstance(data, dict):
        raise ValueError("Ответ модели имеет неправильный формат")

    if "store" not in data:
        data["store"] = "Неизвестный магазин"

    if "total" not in data:
        raise ValueError("Модель не распознала итоговую сумму")

    if "items" not in data:
        data["items"] = []

    try:
        data["total"] = float(data["total"])
    except (TypeError, ValueError):
        raise ValueError("Итоговая сумма имеет неправильный формат")

    if not isinstance(data["items"], list):
        data["items"] = []

    cleaned_items = []
    for item in data["items"]:
        if not isinstance(item, dict):
            continue

        if "name" not in item or "price" not in item:
            continue

        try:
            price = float(item["price"])
        except (TypeError, ValueError):
            continue

        # Считываем категорию от ИИ, если её нет — ставим 'Прочее'
        category = item.get("category", "Прочее")
        if category not in ["Продукты", "Коммуналка", "Транспорт", "Развлечения", "Прочее"]:
            category = "Прочее"

        cleaned_items.append({
            "name": str(item["name"]),
            "price": price,
            "category": category  # Сохраняем категорию
        })

    data["items"] = cleaned_items
    return data



def recognize_receipt(image_path):
    """
    Распознаёт чек по фотографии через OpenRouter.

    Возвращает:

    {
        "store": "...",
        "total": 123.45,
        "items": [...]
    }
    """

    if not API_KEY:
        raise ValueError("OPENROUTER_API_KEY не найден в .env")

    if not os.path.exists(image_path):
        raise FileNotFoundError(
            f"Файл чека не найден: {image_path}"
        )

    # Читаем фотографию
    with open(image_path, "rb") as image_file:
        image_bytes = image_file.read()

    # Кодируем в base64
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:image/jpeg;base64,"
                                f"{image_base64}"
                            )
                        }
                    }
                ]
            }
        ]
    }

    # Делаем несколько попыток.
    # Это особенно полезно для бесплатных моделей,
    # у которых бывают временные rate limit.
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):

        try:
            print(
                f"🤖 Попытка распознавания "
                f"{attempt}/{max_attempts}..."
            )

            response = _send_request(payload)

            # Успешный ответ
            if response.status_code == 200:

                result = response.json()

                text = result["choices"][0]["message"]["content"]

                data = _parse_json(text)

                data = _validate_result(data)

                print("✅ Чек успешно распознан")

                return data

            # Временный лимит
            if response.status_code == 429:

                try:
                    error_data = response.json()

                    retry_after = (
                        error_data
                        .get("error", {})
                        .get("metadata", {})
                        .get("retry_after_seconds", 10)
                    )

                except Exception:
                    retry_after = 10

                retry_after = min(int(retry_after), 30)

                if attempt < max_attempts:
                    print(
                        f"⏳ Модель перегружена. "
                        f"Повтор через {retry_after} сек."
                    )

                    # Так как recognize_receipt пока
                    # синхронная функция, используем sleep.
                    import time
                    time.sleep(retry_after)

                    continue

                raise Exception(
                    "Бесплатная модель временно перегружена. "
                    "Попробуйте отправить чек ещё раз через минуту."
                )

            # Любая другая ошибка API
            raise Exception(
                f"OpenRouter вернул ошибку "
                f"{response.status_code}: {response.text}"
            )

        except requests.Timeout:

            if attempt < max_attempts:
                print("⏳ Таймаут. Повторяем...")
                continue

            raise Exception(
                "Модель слишком долго обрабатывала чек. "
                "Попробуйте отправить фотографию ещё раз."
            )

        except requests.RequestException as e:

            if attempt < max_attempts:
                print(f"🌐 Ошибка соединения: {e}")
                continue

            raise Exception(
                "Не удалось связаться с сервисом распознавания."
            )

        except (KeyError, ValueError, json.JSONDecodeError) as e:

            print(f"⚠️ Ошибка обработки ответа модели: {e}")

            if attempt < max_attempts:
                continue

            raise Exception(
                "Модель вернула неправильный формат данных."
            )

    raise Exception("Не удалось распознать чек.")

def get_ai_savings_advice(rows):
    """
    Принимает список расходов по категориям [(категория, сумма), ...]
    и запрашивает у OpenRouter советы по оптимизации бюджета.
    Делает до 3 попыток при временных ошибках API.
    """
    if not API_KEY:
        raise ValueError("OPENROUTER_API_KEY не найден в .env")
    
    # Формируем читаемый список трат для нейросети
    total = sum(float(amount) for _, amount in rows)
    expenses_string = ""
    for category, amount in rows:
        share = (float(amount) / total) * 100 if total else 0
        expenses_string += f"- {category}: {float(amount):.2f} руб. ({share:.1f}% от общего бюджета)\n"
    
    ai_prompt = f"""
Ты — профессиональный финансовый консультант и эксперт по домашней экономике.
Перед тобой структура расходов за последнюю неделю группы людей (сожители/семья), которые ведут совместный быт и делят траты:
Общая сумма расходов: {total:.2f} рублей.
Распределение по категориям:
{expenses_string}
Твоя задача — проанализировать эти данные и дать КРАТКИЙ, ЖИВОЙ и МАКСИМАЛЬНО ПРАКТИЧНЫЙ совет по оптимизации этого бюджета.
Правила ответа:
Пиши дружелюбно, без занудства, как умный помощник. Используй уместное количество эмодзи.
Обрати внимание на самую большую статью расходов. Предложи 1-2 реальных лайфхака, как её сократить (без экстремальной экономии).
Если есть категория "Прочее" или "Без категории" и она занимает больше 15%, мягко напомни точнее распределять траты, так как там теряются деньги.
Весь ответ должен строго укладываться в 2-4 небольших абзаца (до 800 символов). Текст должен легко читаться с экрана телефона.
Используй Telegram HTML-теги для форматирования: <b>жирный текст</b> для акцентов, <i>курсив</i>. Не используй markdown (типа **, __, `).
"""
    
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": ai_prompt
            }
        ]
    }
    
    # Делаем несколько попыток для подстраховки от временных сбоев
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"💡 Попытка получения совета ИИ {attempt}/{max_attempts}...")
            response = _send_request(payload)
            
            # Успешный ответ
            if response.status_code == 200:
                result = response.json()
                advice = result["choices"][0]["message"]["content"]
                print("✅ Совет ИИ успешно получен")
                return advice.strip()
            
            # Временный лимит (rate limit)
            if response.status_code == 429:
                try:
                    error_data = response.json()
                    retry_after = (
                        error_data
                        .get("error", {})
                        .get("metadata", {})
                        .get("retry_after_seconds", 10)
                    )
                except Exception:
                    retry_after = 10
                retry_after = min(int(retry_after), 30)
                if attempt < max_attempts:
                    print(f"⏳ Модель перегружена. Повтор через {retry_after} сек.")
                    import time
                    time.sleep(retry_after)
                    continue
                raise Exception(
                    "Бесплатная модель временно перегружена. "
                    "Попробуйте получить совет ещё раз через минуту."
                )
            
            # Любая другая ошибка API
            raise Exception(
                f"OpenRouter вернул ошибку {response.status_code}: {response.text}"
            )
        
        except requests.Timeout:
            if attempt < max_attempts:
                print("⏳ Таймаут. Повторяем...")
                continue
            raise Exception(
                "Модель слишком долго обрабатывала запрос. "
                "Попробуйте получить совет ещё раз."
            )
        
        except requests.RequestException as e:
            if attempt < max_attempts:
                print(f"🌐 Ошибка соединения: {e}")
                continue
            raise Exception(
                "Не удалось связаться с сервисом ИИ."
            )
        
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ Ошибка обработки ответа модели: {e}")
            if attempt < max_attempts:
                continue
            raise Exception(
                "Модель вернула неправильный формат данных."
            )
    
    raise Exception("Не удалось получить совет от ИИ.")

def get_ai_forecast_verdict(current_month, forecast_total, percent_diff):
    """
    Генерирует краткий ИИ-комментарий к получившемуся прогнозу расходов.
    Делает до 3 попыток при временных ошибках API.
    """
    if not API_KEY:
        raise ValueError("OPENROUTER_API_KEY не найден в .env")
    
    diff_text = f"на {abs(percent_diff):.0f}% больше" if percent_diff > 0 else f"на {abs(percent_diff):.0f}% меньше"
    if percent_diff == 0:
        diff_text = "столько же, сколько в"
    
    ai_prompt = f"""
Ты — финансовый бот-аналитик. Система посчитала прогноз расходов для группы сожителей до конца месяца.
Вот чистые цифры:
Уже потрачено в этом месяце: {current_month:.2f} руб.
Ожидаемый прогноз до конца месяца (при текущем темпе): {forecast_total:.2f} руб.
Этот прогноз {diff_text} трат прошлого месяца.
Напиши КРАТКИЙ (буквально 2-3 предложения), живой вердикт-комментарий к этому прогнозу.
Если они тратят больше, чем в прошлом месяце — выдай легкое, дружелюбное предупреждение и мотивируй подсократить импульсивные покупки.
Если идут в плюсе или тратят меньше — похвали их за осознанность. Используй эмодзи.
Используй Telegram HTML-теги для форматирования (<b>жирный</b>, <i>курсив</i>). Без markdown.
Ответ должен быть емким и коротким для экрана телефона.
"""
    
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": ai_prompt}]
    }
    
    # Делаем несколько попыток для подстраховки от временных сбоев
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"📈 Попытка получения прогноза ИИ {attempt}/{max_attempts}...")
            response = _send_request(payload)
            
            # Успешный ответ
            if response.status_code == 200:
                verdict = response.json()["choices"][0]["message"]["content"].strip()
                print("✅ Прогноз ИИ успешно получен")
                return verdict
            
            # Временный лимит (rate limit)
            if response.status_code == 429:
                try:
                    error_data = response.json()
                    retry_after = (
                        error_data
                        .get("error", {})
                        .get("metadata", {})
                        .get("retry_after_seconds", 10)
                    )
                except Exception:
                    retry_after = 10
                retry_after = min(int(retry_after), 30)
                if attempt < max_attempts:
                    print(f"⏳ Модель перегружена. Повтор через {retry_after} сек.")
                    import time
                    time.sleep(retry_after)
                    continue
                # Если все попытки исчерпаны — возвращаем fallback
                return "📈 Держите расходы под контролем, чтобы не выйти за рамки комфортного бюджета!"
            
            # Любая другая ошибка API
            raise Exception(
                f"OpenRouter вернул ошибку {response.status_code}: {response.text}"
            )
        
        except requests.Timeout:
            if attempt < max_attempts:
                print("⏳ Таймаут. Повторяем...")
                continue
            # Если все попытки исчерпаны — возвращаем fallback
            return "📈 Держите расходы под контролем, чтобы не выйти за рамки комфортного бюджета!"
        
        except requests.RequestException as e:
            if attempt < max_attempts:
                print(f"🌐 Ошибка соединения: {e}")
                continue
            # Если все попытки исчерпаны — возвращаем fallback
            return "📈 Держите расходы под контролем, чтобы не выйти за рамки комфортного бюджета!"
        
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ Ошибка обработки ответа модели: {e}")
            if attempt < max_attempts:
                continue
            # Если все попытки исчерпаны — возвращаем fallback
            return "📈 Держите расходы под контролем, чтобы не выйти за рамки комфортного бюджета!"
    
    # Fallback на случай, если все попытки не дали результата
    return "📈 Держите расходы под контролем, чтобы не выйти за рамки комфортного бюджета!"