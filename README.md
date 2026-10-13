# Межгородское такси — Telegram-бот

Open-source каркас бота на Python 3.11+, aiogram 3 и PostgreSQL. Пассажир оформляет заявку пошагово, запись сохраняется в PostgreSQL, водители получают карточку в рабочем чате и могут скопировать номер телефона.

## Функции

- Выбор кыргызского или русского языка в `/start`; язык сохраняется в профиле пользователя и базе данных.
- `/start` сбрасывает незавершённый FSM-сценарий и показывает чистое меню. При активной заявке нижняя клавиатура содержит кнопку отмены.
- Главное меню, создание заказа через FSM, контакт или ручной ввод телефона, история заказов.
- Для короткого списка показаны популярные направления, остальные находятся в подменю; маршрут также можно ввести вручную.
- Дата поездки выбирается текстом, время — готовым интервалом либо сеткой часов.
- Направления и базовые цены хранятся в таблице `routes`; при пустой таблице создаются три демонстрационных маршрута с нулевым тарифом.
- Рабочий чат получает маршрут, дату, места, телефон и ссылку на пассажира. В карточке есть кнопка копирования контактного номера.
- `/admin` показывает статистику только пользователям из `TELEGRAM_ADMIN_IDS`; доступны `/routes`, `/setprice`, `/addrout`, `/broadcast`.
- SQLAlchemy 2 async, миграции Alembic, Docker Compose с PostgreSQL volume и health check.

Тарифы сейчас администрируются и сохраняются, но в заказе показывается подтверждение цены водителем: расчет стоимости и платежи не реализованы. FSM хранится в памяти процесса и сбрасывается при перезапуске. Для нескольких реплик бота подключите Redis storage и механизм блокировки обновлений.

## Библиотеки

| Пакет | Для чего |
|---|---|
| `aiogram` | Telegram Bot API, роутеры, фильтры, FSM и клавиатуры |
| `SQLAlchemy` | Асинхронные ORM-модели и запросы |
| `asyncpg` | Драйвер PostgreSQL для asyncio |
| `alembic` | Версионирование схемы БД и миграции |
| `pydantic-settings` | Проверка и загрузка настроек из переменных окружения |
| `python-dotenv` | Загрузка `.env` при локальной разработке (интеграция для settings) |

Диапазоны совместимы с Python 3.11+ и aiogram 3.x. Для кнопки копирования телефона требуется aiogram 3.15 или новее. После обновления зависимостей выполните `pip install -r requirements.txt`, а после добавления миграции языка — `alembic upgrade head`.

Задайте реальное имя поддержки в `SUPPORT_USERNAME` (можно с `@`). Интерфейс пользователя хранит язык в таблице `users`; для новых версий обязательно применяйте все миграции командой `alembic upgrade head`.

## Подготовка Telegram

1. Создайте бота через [@BotFather](https://t.me/BotFather) и скопируйте токен.
2. Водитель должен открыть личный чат с ботом и нажать `/start`, чтобы получать заявки и сообщения о подписке.
3. Узнайте свой Telegram user ID для административной панели.
4. Заполните `TELEGRAM_ADMIN_IDS`. Несколько администраторов указываются через запятую. `DRIVERS_CHAT_ID` пока остаётся обязательной настройкой для совместимости с существующими функциями проекта.

## Локальная установка

```bash
git clone <URL-ВАШЕГО-РЕПОЗИТОРИЯ> taxi_bot
cd taxi_bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Отредактируйте `.env`: задайте `BOT_TOKEN`, `DRIVERS_CHAT_ID`, `TELEGRAM_ADMIN_IDS` и адрес PostgreSQL. Используйте собственный случайный пароль и одинаковые реквизиты в URL и PostgreSQL. Пример URL: `postgresql+asyncpg://taxi:ВАШ_ПАРОЛЬ@localhost:5432/taxi_db`. До локального запуска поднимите сервер PostgreSQL и создайте базу/пользователя. Пример запуска временной БД в Docker с публикацией только на localhost:

```bash
docker run --name taxi-postgres -e POSTGRES_DB=taxi_db -e POSTGRES_USER=taxi -e POSTGRES_PASSWORD='ЗАДАЙТЕ_СЛОЖНЫЙ_ПАРОЛЬ' -p 127.0.0.1:5432:5432 -d postgres:16-alpine
```

Затем запустите миграции и бота:

```bash
alembic upgrade head
python main.py
```

Для остановки бота нажмите Ctrl+C. Остановить временную БД: `docker stop taxi-postgres`.

## Роли водителей и подписки

После миграции `/start` позволяет выбрать режим клиента или водителя. При первом выборе режима водителя бот активирует пробный доступ на 72 часа. Сроки подписки и платежные транзакции хранятся в PostgreSQL. Новые заявки рассылаются только водителям с активной подпиской, которые уже открыли бота в личных сообщениях.

Текущий модуль оплаты — интеграционная заглушка: он создаёт внутреннюю транзакцию и QR-код со ссылкой из `PAYMENT_BASE_URL`, но сам не выставляет настоящий счёт в банке. Перед реальными платежами замените создание ссылки в `bot/services/payment.py` на Merchant API вашего провайдера и настройте его webhook с полями `external_tx_id`, `custom_payload` (Telegram ID водителя), `amount`, `status` (`SUCCESS`/`FAILED`) и `days`. Провайдер должен подписывать исходное тело запроса HMAC-SHA256; подпись передаётся заголовком `X-Webhook-Signature` (можно с префиксом `sha256=`).

Задайте секрет webhook командой `openssl rand -hex 32` и сохраните результат в `PAYMENT_WEBHOOK_SECRET`. Не используйте пустое значение в продакшене: endpoint ответит `503`, пока секрет не настроен. Опубликуйте путь `https://ВАШ_ДОМЕН/webhooks/payments` через HTTPS reverse proxy (например, Nginx или Caddy). В Compose порт приложения `8080` должен быть доступен proxy; сам webhook не следует публиковать по обычному HTTP в открытый интернет.

После обновления проекта примените миграции: `alembic upgrade head` локально либо `docker compose run --rm bot alembic upgrade head` в Compose.

## Docker Compose

Для контейнера `bot` в URL хост базы данных — `postgres`, не `localhost`. Скопируйте пример `.env`, установите токен и ID чата/администратора, а также задайте сильный уникальный `POSTGRES_PASSWORD` и тот же пароль в `DATABASE_URL`. Compose завершится с ошибкой, если обязательные параметры базы данных не указаны; небезопасные пароли по умолчанию удалены.

Сначала примените миграции, затем запустите приложение:

```bash
docker compose run --rm bot alembic upgrade head
docker compose up -d --build
docker compose logs -f bot
```

Обновление: `git pull && docker compose up -d --build`. Остановка: `docker compose down`; данные БД остаются в volume `postgres_data`. `docker compose down -v` удаляет volume и данные.

## Деплой на VPS (Ubuntu 22.04/24.04)

1. Подключитесь: `ssh user@SERVER_IP`. Обновите систему: `sudo apt update && sudo apt upgrade -y`.
2. Установите Docker Engine и Compose plugin по официальной инструкции Docker для Ubuntu: https://docs.docker.com/engine/install/ubuntu/ . Добавьте пользователя в группу Docker (это дает права уровня root): `sudo usermod -aG docker "$USER"`, затем переподключитесь и проверьте `docker compose version`.
3. Установите Git (`sudo apt install -y git`) и клонируйте репозиторий: `git clone <URL-ВАШЕГО-РЕПОЗИТОРИЯ> taxi_bot && cd taxi_bot`.
4. Создайте конфигурацию: `cp .env.example .env`, затем `nano .env`. Укажите настоящий токен, ID чата/администраторов и замените пароль PostgreSQL. `DATABASE_URL` внутри Compose должен использовать hostname `postgres`.
5. Примените миграции: `docker compose run --rm bot alembic upgrade head`. Затем поднимите сервисы: `docker compose up -d --build`.
6. Проверьте состояние: `docker compose ps`, логи: `docker compose logs -f` (или только бота: `docker compose logs -f bot`).
7. Обновление после публикации новых коммитов: `git pull`, `docker compose run --rm bot alembic upgrade head`, затем `docker compose up -d --build`.

Docker restart policy `unless-stopped` поднимет контейнеры после перезагрузки VPS; отдельный systemd unit для Compose обычно не требуется. При необходимости создайте `/etc/systemd/system/taxi-bot.service`:

```ini
[Unit]
Description=Taxi Telegram bot (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/taxi_bot
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

Поместите проект в `/opt/taxi_bot`, замените `WorkingDirectory` при необходимости, затем выполните `sudo systemctl daemon-reload && sudo systemctl enable --now taxi-bot`. Не коммитьте `.env`; храните резервные копии PostgreSQL отдельно от VPS.

## Структура

```text
bot/
  config.py
  handlers/       # start, order, driver, admin, common
  keyboards/      # inline.py, reply.py
  middlewares/    # DB-сессия на update
  states/         # FSM заказа
  database/       # модели, async engine, DAO
alembic/          # окружение и миграции
main.py
Dockerfile
docker-compose.yml
```

## Важные настройки безопасности и эксплуатации

- Ограничьте доступ к рабочему чату и задайте реальные Telegram ID администраторов.
- Не публикуйте порт PostgreSQL в интернет. `.env` содержит секреты и не должен попадать в Git.
- Сделайте резервное копирование volume/БД и проверьте восстановление перед эксплуатацией.
- Бот использует long polling: для одного токена запускайте один экземпляр.
- Уведомление пассажиру возможно только после того, как пассажир открыл диалог с ботом и нажал `/start`.
