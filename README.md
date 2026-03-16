# 🔐 Outline VPN Manager Bot

Telegram-бот для управления сервером [Outline VPN](https://getoutline.org/) через Management API. Позволяет создавать, удалять и настраивать ключи доступа, следить за трафиком и управлять настройками сервера — прямо из Telegram.

---

## ✨ Возможности

### Управление ключами
- 📋 Просмотр списка всех ключей
- ➕ Создание ключа — быстро или с настройками (имя + лимит)
- ✏️ Переименование ключа
- 📊 Установка / снятие лимита трафика на ключ
- 📤 Просмотр использованного трафика с процентом от лимита
- 🗑 Удаление с подтверждением
- 🔗 Ключ доступа `ss://...` прямо в карточке

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

### Безопасность
- 🔒 Доступ только для указанных Telegram ID
- 🔑 SSL certificate pinning — проверка по `outline_cert.pem`
- 📁 `.env` и логи защищены `.gitignore`

---

## 🚀 Установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/p152/outline-vpn-bot.git
cd outline-vpn-bot
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
| `OUTLINE_API_URL` | Outline Manager → Настройки → *URL для доступа к Management API* |
| `ALLOWED_IDS` | [@userinfobot](https://t.me/userinfobot) — покажет ваш ID |

### 4. Получить SSL-сертификат сервера

Outline использует самоподписанный сертификат. Нужно его загрузить один раз:

```bash
python -c "
import ssl
cert = ssl.get_server_certificate(('YOUR_SERVER_IP', YOUR_PORT))
open('outline_cert.pem', 'w').write(cert)
print('Сертификат сохранён')
"
```

> Замените `YOUR_SERVER_IP` и `YOUR_PORT` на IP и порт из `OUTLINE_API_URL`.

### 5. Запустить бота

```bash
python main.py
```

---

## 📁 Структура проекта

```
outline-vpn-bot/
├── main.py              # Основной файл бота
├── requirements.txt     # Зависимости
├── .env                 # Конфигурация (не хранить в git)
├── .env.example         # Шаблон конфигурации
├── .gitignore
├── outline_cert.pem     # SSL-сертификат Outline-сервера
└── bot.log              # Лог (ротация 5 MB × 3 файла)
```

---

## ⚙️ Как работает SSL

Outline Manager использует **certificate pinning**: вместо проверки hostname проверяется, что сервер предъявляет именно тот сертификат, который был загружен при настройке. Это защищает от MITM-атак с подменой сертификата, даже несмотря на то, что сертификат самоподписанный.

При обновлении сервера (переустановка Outline) сертификат меняется — нужно повторно выполнить шаг 4.

---

## 🔄 Обновление сертификата

Если бот перестал подключаться к API после обновления сервера:

```bash
python -c "
import ssl
cert = ssl.get_server_certificate(('YOUR_SERVER_IP', YOUR_PORT))
open('outline_cert.pem', 'w').write(cert)
print('Сертификат обновлён')
"
```

---

## 📦 Зависимости

| Пакет | Версия | Назначение |
|---|---|---|
| [aiogram](https://github.com/aiogram/aiogram) | ≥ 3.7 | Telegram Bot API |
| [aiohttp](https://github.com/aio-libs/aiohttp) | ≥ 3.9 | Async HTTP-клиент для Outline API |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | ≥ 1.0 | Загрузка `.env` |

---

## 🖥 Системные требования

- Python 3.11+
- Доступ к Management API Outline-сервера (порт открыт в firewall)

---

## 📝 Лицензия

MIT
