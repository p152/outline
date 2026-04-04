# 🔐 Outline VPN Manager Bot

Telegram-бот для управления одним или несколькими серверами [Outline VPN](https://getoutline.org/) через Management API. Позволяет создавать, удалять и настраивать ключи доступа, следить за трафиком и управлять настройками сервера — прямо из Telegram.

---

## ✨ Возможности

### Несколько серверов
- 🖥 Список серверов при старте и кнопка **«Сменить сервер»**
- ➕ Добавление сервера по URL Management API прямо в боте
- 🗑 Удаление записи о сервере из бота (сами ключи на Outline не трогаются)
- 📁 Конфигурация серверов хранится в `servers.json` (не коммитится в git)

### Управление ключами
- 📋 Просмотр списка всех ключей
- ➕ Создание ключа — быстро или с настройками (имя + лимит)
- ✏️ Переименование ключа
- 📊 Установка / снятие лимита трафика на ключ
- 📤 Просмотр использованного трафика с процентом от лимита
- 🗑 Удаление с подтверждением
- 🔗 Ключ доступа `ss://...` прямо в карточке
- 🔀 Пресеты и свой hex-префикс ссылки (DPI bypass)

### Статистика
- 📶 Трафик по всем ключам с именами (сортировка по убыванию)
- 💾 Суммарный трафик по серверу
- 🌍 Текущий глобальный лимит
- 🖥 Информация о сервере: версия, hostname, порт, дата создания
- 🟢/🔴 Статус сбора метрик

### Настройки сервера
- ✏️ Переименование сервера
- 🌐 Изменение hostname для ключей доступа
- 🚪 Изменение порта для новых ключей
- 🌍 Глобальный лимит для всех ключей (пресеты 1/5/10/50 GB или своё значение)
- 📶 Включение / выключение сбора метрик
- 🏷 Префикс имён при быстром создании ключей

### Безопасность
- 🔒 Доступ только для указанных Telegram ID
- 🔑 Проверка TLS по PEM-сертификату (pinning)
- 📁 `.env`, `servers.json`, логи и PEM в `.gitignore`

---

## 📄 Файл `servers.json` — когда появляется

Файл **создаётся автоматически**, вы вручную его не обязаны заводить:

1. **Первый запуск бота**, если есть `OUTLINE_API_URL` в `.env`, а `servers.json` ещё нет — бот **мигрирует** этот URL в одну запись в `servers.json` (имя по умолчанию «Outline Server»).
2. **Добавление сервера в боте** («➕ Добавить сервер») — если файла не было, он будет **создан** при сохранении первого сервера.

Если ни `OUTLINE_API_URL`, ни серверов в боте нет — `servers.json` не появится, пока вы не добавите сервер через интерфейс или не скопируете готовый файл.

Формат записи (для справки; про поле `cert` см. ниже):

```json
{
  "servers": [
    {
      "id": "default",
      "name": "Мой VPS",
      "url": "https://IP:PORT/SECRET_PATH",
      "cert": "certs/server1.pem"
    }
  ]
}
```

Поле `"cert"` **необязательно**: если его нет, используется глобальный путь из `OUTLINE_CERT` или `outline_cert.pem`.

---

## 🚀 Установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/p152/outline.git
cd outline
```

### 2. Создать виртуальное окружение и установить зависимости

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Настроить переменные окружения

```bash
cp .env.example .env
```

Открыть `.env` и заполнить:

```env
BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OUTLINE_API_URL=https://YOUR_SERVER_IP:PORT/YOUR_SECRET_PATH
ALLOWED_IDS=123456789
```

| Переменная | Где взять |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OUTLINE_API_URL` | Опционально после миграции в `servers.json`; удобен для **первого** запуска и для скрипта сертификата |
| `ALLOWED_IDS` | [@userinfobot](https://t.me/userinfobot) — покажет ваш ID |
| `OUTLINE_CERT` | (опционально) путь к PEM по умолчанию, если не задано — `outline_cert.pem` |
| `KEY_PREFIX` | (опционально) префикс имён быстрых ключей |

### 4. Получить SSL-сертификат Management API

Outline отдаёт API по HTTPS с **самоподписанным** сертификатом. Чтобы бот проверял TLS, сохраните его PEM на машину с ботом.

**Рекомендуемый способ** — скрипт из репозитория (из корня проекта, venv активирован):

```bash
# URL из Outline Manager или как в OUTLINE_API_URL
python scripts/fetch_outline_cert.py --url "https://YOUR_SERVER_IP:PORT/YOUR_SECRET_PATH"

# или взять URL из .env автоматически
python scripts/fetch_outline_cert.py --from-env
```

По умолчанию создаётся файл `outline_cert.pem` в **текущей рабочей директории**. Запускайте бот и скрипт из одного каталога (обычно корень проекта) или укажите путь явно:

```bash
python scripts/fetch_outline_cert.py --from-env -o outline_cert.pem
python scripts/fetch_outline_cert.py -u "https://..." -o certs/second.pem
```

Для **нескольких** Outline-серверов у каждого свой сертификат: сохраните разные PEM (например `certs/nl.pem`, `certs/de.pem`) и пропишите путь в поле `"cert"` соответствующей записи в `servers.json`. Общий файл по умолчанию задаётся через `OUTLINE_CERT` в `.env`.

Если PEM нет, бот всё равно запустится, но в логе будет предупреждение, что проверка TLS отключена.

### 5. Запустить бота

```bash
python main.py
```

После первого запуска с заданным `OUTLINE_API_URL` проверьте наличие `servers.json` — дальше список серверов можно вести через бота.

---

## 📁 Структура проекта

```
outline/
├── main.py                      # Точка входа бота
├── scripts/
│   └── fetch_outline_cert.py   # Скачивание PEM с хоста Management API
├── requirements.txt
├── .env                         # Секреты (не в git)
├── .env.example
├── .gitignore
├── servers.json                 # Список серверов (создаётся ботом, не в git)
├── outline_cert.pem             # PEM по умолчанию (не в git)
├── certs/                       # Опционально: PEM на каждый сервер (*.pem не в git)
└── bot.log                      # Лог (ротация 5 MB × 3 файла)
```

---

## ⚙️ Как работает SSL

Outline Manager использует проверку по загруженному сертификату: клиент убеждается, что сервер предъявляет тот же PEM, что вы сохранили. Это снижает риск MITM при самоподписанном сертификате.

После переустановки Outline или смены сертификата на сервере выполните скрипт снова и перезапишите соответствующий PEM.

---

## 🔄 Обновление сертификата

```bash
python scripts/fetch_outline_cert.py --from-env -o outline_cert.pem
# или
python scripts/fetch_outline_cert.py -u "https://IP:PORT/SECRET" -o outline_cert.pem
```

---

## 📦 Зависимости

| Пакет | Версия | Назначение |
|---|---|---|
| [aiogram](https://github.com/aiogram/aiogram) | ≥ 3.7 | Telegram Bot API |
| [aiohttp](https://github.com/aio-libs/aiohttp) | ≥ 3.9 | Async HTTP-клиент для Outline API |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | ≥ 1.0 | Загрузка `.env` |

Скрипт `fetch_outline_cert.py` с флагом `--from-env` использует тот же `python-dotenv` из `requirements.txt`.

---

## 🖥 Системные требования

- Python 3.11+
- Доступ к Management API Outline-сервера (порт открыт в firewall)

---

## 📝 Лицензия

MIT
