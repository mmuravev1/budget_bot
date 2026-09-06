import aiosqlite


DB_NAME = "bot.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                UNIQUE(chat_id, user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS receipts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                file_id TEXT,
                image_path TEXT,
                store TEXT,
                total REAL,
                processed INTEGER DEFAULT 0,
                payer_id INTEGER,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Миграции для уже существующей базы.
        try:
            await db.execute("ALTER TABLE receipts ADD COLUMN payer_id INTEGER")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE receipts ADD COLUMN category TEXT")
        except Exception:
            pass
        try:  # [NEW] категория у каждого товара из чека
            await db.execute("ALTER TABLE receipt_items ADD COLUMN category TEXT")  # [NEW]
        except Exception:  # [NEW]
            pass  # [NEW]

        await db.execute("""
            CREATE TABLE IF NOT EXISTS receipt_members (
                receipt_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (receipt_id, user_id)
            )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS receipt_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER,
            name TEXT,
            price REAL,
            category TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS receipt_item_members (
            item_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (item_id, user_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS debts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            from_user INTEGER,
            to_user INTEGER,
            receipt_id INTEGER,
            amount REAL,
            is_paid INTEGER DEFAULT 0
        )
        """)

        await db.commit()


async def add_group(chat_id: int, title: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO groups (chat_id, title)
            VALUES (?, ?)
        """, (chat_id, title))

        await db.commit()


async def add_member(
    chat_id: int,
    user_id: int,
    username: str | None,
    first_name: str
):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO members
            (chat_id, user_id, username, first_name)
            VALUES (?, ?, ?, ?)
        """, (
            chat_id,
            user_id,
            username,
            first_name
        ))

        await db.commit()


async def get_members(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT user_id, username, first_name
            FROM members
            WHERE chat_id = ?
        """, (chat_id,))

        return await cursor.fetchall()


async def add_receipt(chat_id, user_id, file_id, image_path):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO receipts
            (chat_id, user_id, file_id, image_path)
            VALUES (?, ?, ?, ?)
        """, (
            chat_id,
            user_id,
            file_id,
            image_path
        ))

        await db.commit()

async def get_receipts(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT r.id, r.image_path, r.created_at,
                   r.user_id, m.username, m.first_name,
                   r.store, r.total, r.payer_id
            FROM receipts r
            LEFT JOIN members m
              ON m.chat_id = r.chat_id AND m.user_id = r.user_id
            WHERE r.chat_id = ?
            ORDER BY r.created_at DESC
            LIMIT 10
        """, (chat_id,))
        return await cursor.fetchall()


async def get_receipt_items(receipt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, name, price, category
            FROM receipt_items
            WHERE receipt_id = ?
            ORDER BY id ASC
        """, (receipt_id,))  # [CHG] отдаём категорию товара для карточки чека
        return await cursor.fetchall()


async def get_my_receipts(chat_id: int, user_id: int):
    """Возвращает последние покупки, созданные пользователем.

    В таблице receipts хранятся и чеки, и покупки, добавленные вручную,
    поэтому ручной ввод автоматически попадает в личную историю.
    """
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, created_at, store, total, payer_id,
                   CASE
                       WHEN image_path IS NULL THEN 'manual'
                       ELSE 'receipt'
                   END AS source
            FROM receipts
            WHERE chat_id = ?
              AND user_id = ?
              AND processed = 1
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 20
        """, (chat_id, user_id))

        return await cursor.fetchall()


async def get_my_debts(chat_id: int, user_id: int):
    """Возвращает непогашенные долги, где пользователь является должником."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT d.id, d.to_user, d.amount, d.receipt_id, r.created_at, r.store
            FROM debts d
            LEFT JOIN receipts r ON r.id = d.receipt_id
            WHERE d.chat_id = ?
              AND d.from_user = ?
              AND d.is_paid = 0
              AND d.amount > 0
            ORDER BY datetime(r.created_at) DESC, d.id DESC
        """, (chat_id, user_id))

        return await cursor.fetchall()

async def link_member_to_receipt(receipt_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO receipt_members
            (receipt_id, user_id)
            VALUES (?, ?)
        """, (receipt_id, user_id))

        await db.commit()


async def get_last_receipt(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id
            FROM receipts
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (chat_id,))

        return await cursor.fetchone()

async def get_receipt_path(receipt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT image_path
            FROM receipts
            WHERE id=?
        """, (receipt_id,))

        row = await cursor.fetchone()

        return row[0]

async def set_receipt_payer(receipt_id: int, payer_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE receipts
            SET payer_id = ?
            WHERE id = ?
        """, (payer_id, receipt_id))
        await db.commit()


async def get_receipt_item_participants(receipt_id: int):
    """Возвращает участников каждого товара: {item_id: [user_id, ...]}."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT ri.id, rim.user_id
            FROM receipt_items ri
            LEFT JOIN receipt_item_members rim ON rim.item_id = ri.id
            WHERE ri.receipt_id = ?
            ORDER BY ri.id, rim.user_id
        """, (receipt_id,))
        rows = await cursor.fetchall()

    result = {}
    for item_id, user_id in rows:
        result.setdefault(item_id, [])
        if user_id is not None:
            result[item_id].append(user_id)
    return result


async def get_receipt_participants(receipt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT m.user_id, m.username, m.first_name
            FROM receipt_members rm
            JOIN members m ON m.user_id = rm.user_id
            JOIN receipts r
              ON r.id = rm.receipt_id
             AND m.chat_id = r.chat_id
            WHERE rm.receipt_id = ?
            ORDER BY m.first_name ASC
        """, (receipt_id,))
        return await cursor.fetchall()


async def add_item(receipt_id: int, name: str, price: float, category: str | None = None):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
        INSERT INTO receipt_items
        (receipt_id,name,price,category)
        VALUES(?,?,?,?)
        """, (receipt_id, name, price, category))
        await db.commit()
        return cursor.lastrowid


async def link_member_to_item(item_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO receipt_item_members (item_id, user_id)
            VALUES (?, ?)
        """, (item_id, user_id))
        await db.commit()


async def update_receipt_total(
    receipt_id: int,
    total: float,
    store: str,
    category: str | None = None
):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            UPDATE receipts
            SET total=?, store=?, category=?, processed=1
            WHERE id=?
            """,
            (total, store, category, receipt_id)
        )
        await db.commit()

async def add_debt(
    chat_id:int,
    from_user:int,
    to_user:int,
    receipt_id:int,
    amount:float
):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        INSERT INTO debts
        (chat_id,from_user,to_user,receipt_id,amount)
        VALUES(?,?,?,?,?)
        """,(chat_id,from_user,to_user,receipt_id,amount))

        await db.commit()


async def get_debts(chat_id:int):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
        SELECT from_user,to_user,amount
        FROM debts
        WHERE chat_id=? AND is_paid=0
        """,(chat_id,))

        return await cursor.fetchall()

async def get_all_debts(chat_id: int):
    """Возвращает все долги без взаимозачёта и оптимизации."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT from_user, to_user, amount, receipt_id
            FROM debts
            WHERE chat_id = ? AND is_paid = 0 AND amount > 0
            ORDER BY id DESC
        """, (chat_id,))
        return await cursor.fetchall()

async def clear_debts(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            DELETE FROM debts
            WHERE chat_id = ?
            """,
            (chat_id,)
        )

        await db.commit()

async def clear_members(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            DELETE FROM members
            WHERE chat_id = ?
            """,
            (chat_id,)
        )
        await db.commit()


# [NEW] Удаляет чеки и товары группы, долги не трогает
async def clear_expenses(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:  # [NEW]
        await db.execute(
            """
            DELETE FROM receipt_item_members
            WHERE item_id IN (
                SELECT ri.id FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                WHERE r.chat_id = ?
            )
            """,
            (chat_id,),
        )
        await db.execute(  # [NEW] сначала товары, чтобы не оставить «осиротевшие» позиции
            """
            DELETE FROM receipt_items
            WHERE receipt_id IN (
                SELECT id FROM receipts WHERE chat_id = ?
            )
            """,
            (chat_id,),
        )
        await db.execute(  # [NEW] кто участвовал в каких чеках
            """
            DELETE FROM receipt_members
            WHERE receipt_id IN (
                SELECT id FROM receipts WHERE chat_id = ?
            )
            """,
            (chat_id,),
        )
        await db.execute(  # [NEW] сами чеки и ручные покупки
            """
            DELETE FROM receipts
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        await db.commit()  # [NEW]


# [CHG] Суммы по категориям за последние 7 дней: ручные покупки + чеки
async def get_category_totals_last_week(chat_id: int):
    # [CHG] Открываем соединение с базой
    async with aiosqlite.connect(DB_NAME) as db:
        # [CHG] Переписан SQL-запрос: теперь он объединяет категории товаров и общие категории покупок без позиций
        cursor = await db.execute(
            """
            SELECT category_name, SUM(amount) AS amount
            FROM (
                -- ЧАСТЬ 1: Собираем категории из отдельных товаров чеков (для распознанных фото-чеков)
                SELECT
                    COALESCE(NULLIF(TRIM(ri.category), ''), NULLIF(TRIM(r.category), ''), 'Без категории') AS category_name,
                    ri.price AS amount
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                WHERE r.chat_id = ?
                  AND r.processed = 1
                  AND ri.price IS NOT NULL
                  AND ri.price > 0
                  AND datetime(r.created_at) >= datetime('now', '-7 days')

                UNION ALL

                -- ЧАСТЬ 2 [FIX]: Собираем категории напрямую из чеков/покупок (для ручного ввода, где нет позиций товаров)
                SELECT
                    COALESCE(NULLIF(TRIM(r.category), ''), 'Без категории') AS category_name,
                    r.total AS amount
                FROM receipts r
                WHERE r.chat_id = ?
                  AND r.processed = 1
                  AND r.total IS NOT NULL
                  AND r.total > 0
                  AND datetime(r.created_at) >= datetime('now', '-7 days')
                  -- [FIX] Важное условие: берем запись из receipts, ТОЛЬКО если для неё нет записей в receipt_items
                  -- Это исключает двойной учет суммы (и за чек целиком, и за его товары отдельно)
                  AND NOT EXISTS (
                      SELECT 1 FROM receipt_items ri WHERE ri.receipt_id = r.id
                  )
            ) AS cat_rows
            GROUP BY category_name
            ORDER BY amount DESC
            """,
            (chat_id, chat_id),  # [CHG] Передаем chat_id дважды: первый для ЧАСТИ 1, второй для ЧАСТИ 2
        )
        # [CHG] Возвращаем список кортежей (название категории, сумма) в функцию построения графика
        return await cursor.fetchall()

# [NEW] Получение общей суммы трат за последние 7 дней и за прошлый месяц
async def get_budget_forecast_data(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Сумма за последние 7 дней (текущий темп)
        cursor_current = await db.execute("""
            SELECT SUM(amount) FROM (
                SELECT ri.price AS amount FROM receipt_items ri 
                JOIN receipts r ON r.id = ri.receipt_id 
                WHERE r.chat_id = ? AND r.processed = 1 AND datetime(r.created_at) >= datetime('now', '-7 days')
                UNION ALL
                SELECT r.total AS amount FROM receipts r 
                WHERE r.chat_id = ? AND r.processed = 1 AND datetime(r.created_at) >= datetime('now', '-7 days')
                AND NOT EXISTS (SELECT 1 FROM receipt_items ri WHERE ri.receipt_id = r.id)
            )
        """, (chat_id, chat_id))
        current_week_total = (await cursor_current.fetchone())[0] or 0.0

        # 2. Сумма за весь текущий календарный месяц (с 1 числа)
        cursor_month = await db.execute("""
            SELECT SUM(amount) FROM (
                SELECT ri.price AS amount FROM receipt_items ri 
                JOIN receipts r ON r.id = ri.receipt_id 
                WHERE r.chat_id = ? AND r.processed = 1 AND datetime(r.created_at) >= datetime('now', 'start of month')
                UNION ALL
                SELECT r.total AS amount FROM receipts r 
                WHERE r.chat_id = ? AND r.processed = 1 AND datetime(r.created_at) >= datetime('now', 'start of month')
                AND NOT EXISTS (SELECT 1 FROM receipt_items ri WHERE ri.receipt_id = r.id)
            )
        """, (chat_id, chat_id))
        current_month_total = (await cursor_month.fetchone())[0] or 0.0

        # 3. Сумма за прошлый календарный месяц (для сравнения)
        cursor_last_month = await db.execute("""
            SELECT SUM(amount) FROM (
                SELECT ri.price AS amount FROM receipt_items ri 
                JOIN receipts r ON r.id = ri.receipt_id 
                WHERE r.chat_id = ? AND r.processed = 1 
                  AND datetime(r.created_at) >= datetime('now', 'start of month', '-1 month')
                  AND datetime(r.created_at) < datetime('now', 'start of month')
                UNION ALL
                SELECT r.total AS amount FROM receipts r 
                WHERE r.chat_id = ? AND r.processed = 1 
                  AND datetime(r.created_at) >= datetime('now', 'start of month', '-1 month')
                  AND datetime(r.created_at) < datetime('now', 'start of month')
                AND NOT EXISTS (SELECT 1 FROM receipt_items ri WHERE ri.receipt_id = r.id)
            )
        """, (chat_id, chat_id))
        last_month_total = (await cursor_last_month.fetchone())[0] or 0.0

        return current_week_total, current_month_total, last_month_total
