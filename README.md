# Discord + Telegram Role Sound Bot

Бот проигрывает индивидуальный звук при входе пользователя в голосовой канал Discord.
Звук назначается на **роль** через Telegram-бота.

## Что реализовано

- Discord-бот отслеживает вход в голосовые каналы (`on_voice_state_update`).
- Для каждой роли сервера можно привязать свой аудиофайл.
- Если у пользователя несколько ролей — используется звук роли с максимальным приоритетом (по позиции роли).
- Telegram-бот с основной кнопкой:
  1. `Роль` — показать роли сервера кнопками, выбрать нужную роль (или кнопку `Без роли`) и сразу загрузить для этого сценария аудио.
  2. `Убрать звук` — выбрать роль кнопкой и удалить привязанный к ней звук.
- Проверка, что бот добавлен на сервер (`DISCORD_GUILD_ID`).

## Требования

- Python 3.11+
- FFmpeg установлен в системе (для воспроизведения звука в Discord)

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env`:

- `DISCORD_TOKEN` — токен Discord-бота
- `TELEGRAM_TOKEN` — токен Telegram-бота
- `DISCORD_GUILD_ID` — ID вашего Discord сервера
- `TELEGRAM_ADMIN_IDS` — Telegram user id админов через запятую

## Запуск

```bash
python bot.py
```

## Telegram сценарий

1. Нажмите `Роль`.
2. Нажмите кнопку нужной роли в Telegram (без копирования ID) или кнопку `Без роли`.
3. Отправьте аудиофайл или voice-сообщение (кнопка `Назад` также доступна).
4. Бот сохранит файл и привяжет его к выбранной роли/сценарию `Без роли`.

### Удаление звука

1. Нажмите `Убрать звук`.
2. Выберите роль кнопкой.
3. Бот удалит звук и отправит сообщение, что звук с данной роли удален.


## Автозапуск на Windows (при включении ПК)

Ниже готовый сценарий, чтобы бот стартовал автоматически при входе в Windows:

1. Подготовьте проект (один раз):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Проверьте ручной запуск:

```powershell
.\run_bot.bat
```

3. Установите задачу автозапуска (PowerShell от имени администратора):

```powershell
.\scripts\windows\install_startup_task.ps1
```

4. Проверка задачи:

```powershell
Get-ScheduledTask -TaskName DiscordTelegramRoleSoundBot
```

5. Запустить задачу вручную для теста:

```powershell
Start-ScheduledTask -TaskName DiscordTelegramRoleSoundBot
```

### Как удалить автозапуск

```powershell
Unregister-ScheduledTask -TaskName DiscordTelegramRoleSoundBot -Confirm:$false
```

## Примечания

- Хранилище: SQLite (`bot.db`) и папка с файлами (`audio/`).
- Для slash-команды `/роль_звуки` у бота должны быть права на использование application commands.
