import asyncio
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("discord_telegram_sound_bot")

CHOOSE_ROLE, WAIT_AUDIO = range(2)


@dataclass
class Config:
    discord_token: str
    telegram_token: str
    guild_id: int
    admin_ids: set[int]
    audio_dir: Path
    db_path: Path


def load_config() -> Config:
    load_dotenv()
    discord_token = os.getenv("DISCORD_TOKEN", "")
    telegram_token = os.getenv("TELEGRAM_TOKEN", "")
    guild_id = int(os.getenv("DISCORD_GUILD_ID", "0"))
    raw_admins = os.getenv("TELEGRAM_ADMIN_IDS", "")
    admin_ids = {int(x.strip()) for x in raw_admins.split(",") if x.strip()}
    audio_dir = Path(os.getenv("AUDIO_DIR", "audio"))
    db_path = Path(os.getenv("DB_PATH", "bot.db"))

    if not discord_token or not telegram_token or guild_id == 0 or not admin_ids:
        raise ValueError(
            "Заполните переменные DISCORD_TOKEN, TELEGRAM_TOKEN, DISCORD_GUILD_ID и TELEGRAM_ADMIN_IDS"
        )

    return Config(
        discord_token=discord_token,
        telegram_token=telegram_token,
        guild_id=guild_id,
        admin_ids=admin_ids,
        audio_dir=audio_dir,
        db_path=db_path,
    )


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS role_sounds (
                role_id INTEGER PRIMARY KEY,
                role_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    def set_role_sound(self, role_id: int, role_name: str, file_path: str) -> None:
        self.conn.execute(
            """
            INSERT INTO role_sounds(role_id, role_name, file_path)
            VALUES (?, ?, ?)
            ON CONFLICT(role_id) DO UPDATE SET
                role_name=excluded.role_name,
                file_path=excluded.file_path,
                updated_at=CURRENT_TIMESTAMP
            """,
            (role_id, role_name, file_path),
        )
        self.conn.commit()

    def get_role_sound(self, role_id: int) -> Optional[str]:
        cur = self.conn.execute(
            "SELECT file_path FROM role_sounds WHERE role_id = ?",
            (role_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def list_role_sounds(self) -> list[tuple[int, str, str]]:
        cur = self.conn.execute(
            "SELECT role_id, role_name, file_path FROM role_sounds ORDER BY role_name"
        )
        return list(cur.fetchall())


class DiscordTelegramSoundBot:
    def __init__(self, config: Config):
        self.config = config
        self.storage = Storage(config.db_path)
        config.audio_dir.mkdir(parents=True, exist_ok=True)

        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True

        self.discord_bot = commands.Bot(command_prefix="!", intents=intents)
        self.telegram_app = Application.builder().token(config.telegram_token).build()
        self.voice_clients: Dict[int, discord.VoiceClient] = {}

        self._register_discord_events()
        self._register_telegram_handlers()

    def _register_discord_events(self) -> None:
        @self.discord_bot.event
        async def on_ready() -> None:
            logger.info("Discord бот запущен как %s", self.discord_bot.user)
            guild = self.discord_bot.get_guild(self.config.guild_id)
            if guild is None:
                logger.error(
                    "Бот не найден на сервере с ID=%s. Добавьте бота на сервер.",
                    self.config.guild_id,
                )
            else:
                logger.info("Подключен к серверу: %s", guild.name)

            try:
                synced = await self.discord_bot.tree.sync()
                logger.info("Синхронизировано slash-команд: %s", len(synced))
            except Exception as exc:
                logger.exception("Не удалось синхронизировать slash-команды: %s", exc)

        @self.discord_bot.event
        async def on_voice_state_update(
            member: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState,
        ) -> None:
            if member.bot:
                return
            if before.channel == after.channel or after.channel is None:
                return

            sound_path = self._find_sound_for_member(member)
            if sound_path is None:
                return

            await self._play_sound(after.channel, sound_path)

        @self.discord_bot.tree.command(
            name="роль_звуки", description="Показать роли с назначенными звуками"
        )
        async def role_sounds(interaction: discord.Interaction) -> None:
            rows = self.storage.list_role_sounds()
            if not rows:
                await interaction.response.send_message(
                    "Пока не назначено ни одного звука.", ephemeral=True
                )
                return
            text = "\n".join(f"• {name} (`{rid}`) -> {path}" for rid, name, path in rows)
            await interaction.response.send_message(
                f"Назначенные звуки:\n{text}", ephemeral=True
            )

    def _register_telegram_handlers(self) -> None:
        self.telegram_app.add_handler(CommandHandler("start", self.tg_start))
        self.telegram_app.add_handler(CommandHandler("help", self.tg_help))

        conv = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(r"^Роль$"), self.tg_role_list),
            ],
            states={
                CHOOSE_ROLE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.tg_choose_role)
                ],
                WAIT_AUDIO: [
                    MessageHandler(
                        (filters.AUDIO | filters.VOICE | filters.TEXT) & ~filters.COMMAND,
                        self.tg_receive_audio,
                    )
                ],
            },
            fallbacks=[
                CommandHandler("cancel", self.tg_cancel),
                MessageHandler(filters.Regex(r"^Назад$"), self.tg_back_to_main_menu),
            ],
            per_user=True,
        )
        self.telegram_app.add_handler(conv)

    def _tg_is_admin(self, user_id: int) -> bool:
        return user_id in self.config.admin_ids

    def _tg_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton("Роль")]],
            resize_keyboard=True,
        )

    def _tg_back_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton("Назад")]],
            resize_keyboard=True,
        )

    def _tg_role_keyboard(self, guild: discord.Guild) -> ReplyKeyboardMarkup:
        buttons = [
            [KeyboardButton(f"{role.name} ({role.id})")]
            for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if role.name != "@everyone"
        ]
        buttons.append([KeyboardButton("Назад")])
        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    async def tg_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or not self._tg_is_admin(user.id):
            await update.message.reply_text("Доступ запрещен.")
            return
        await update.message.reply_text(
            "Привет! Нажмите «Роль», выберите роль кнопкой и загрузите звук.",
            reply_markup=self._tg_keyboard(),
        )

    async def tg_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "1. Роль — выбрать роль сервера кнопкой.\n"
            "2. После выбора роли отправьте аудиофайл или voice.",
            reply_markup=self._tg_keyboard(),
        )

    async def tg_role_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if update.effective_user is None or not self._tg_is_admin(update.effective_user.id):
            await update.message.reply_text("Доступ запрещен.")
            return ConversationHandler.END

        guild = self.discord_bot.get_guild(self.config.guild_id)
        if guild is None:
            await update.message.reply_text("Discord-бот не подключен к серверу.")
            return ConversationHandler.END

        await update.message.reply_text(
            "Выберите роль кнопкой ниже, затем отправьте звук для этой роли.",
            reply_markup=self._tg_role_keyboard(guild),
        )
        return CHOOSE_ROLE

    async def tg_back_to_main_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data.pop("selected_role_id", None)
        context.user_data.pop("selected_role_name", None)
        await update.message.reply_text(
            "Возвращаю в главное меню. Выберите: Роль.",
            reply_markup=self._tg_keyboard(),
        )
        return ConversationHandler.END

    async def tg_choose_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = (update.message.text or "").strip()
        if text == "Назад":
            return await self.tg_back_to_main_menu(update, context)

        match = re.search(r"\((\d+)\)$", text)
        if not match:
            await update.message.reply_text(
                "Пожалуйста, нажмите кнопку роли из списка или «Назад».",
            )
            return CHOOSE_ROLE

        role_id = int(match.group(1))
        guild = self.discord_bot.get_guild(self.config.guild_id)
        role = guild.get_role(role_id) if guild else None
        if role is None:
            await update.message.reply_text(
                "Роль не найдена. Выберите роль кнопкой из списка или нажмите «Назад».",
                reply_markup=self._tg_role_keyboard(guild) if guild else self._tg_back_keyboard(),
            )
            return CHOOSE_ROLE

        context.user_data["selected_role_id"] = role.id
        context.user_data["selected_role_name"] = role.name
        await update.message.reply_text(
            f"Роль выбрана: {role.name}. Теперь отправьте аудиофайл или voice-сообщение, либо нажмите «Назад».",
            reply_markup=self._tg_back_keyboard(),
        )
        return WAIT_AUDIO

    async def tg_receive_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = (update.message.text or "").strip()
        if text == "Назад":
            return await self.tg_back_to_main_menu(update, context)

        role_id = context.user_data.get("selected_role_id")
        role_name = context.user_data.get("selected_role_name")
        if not role_id:
            await update.message.reply_text("Сначала выберите роль через кнопку «Роль».")
            return ConversationHandler.END

        audio = update.message.audio
        voice = update.message.voice
        if audio is None and voice is None:
            await update.message.reply_text(
                "Отправьте аудиофайл или voice.", reply_markup=self._tg_back_keyboard()
            )
            return WAIT_AUDIO

        media = audio if audio is not None else voice
        tg_file = await context.bot.get_file(media.file_id)
        ext = "ogg" if voice else (audio.file_name.split(".")[-1] if audio.file_name else "mp3")
        target = self.config.audio_dir / f"role_{role_id}.{ext}"
        await tg_file.download_to_drive(custom_path=str(target))

        self.storage.set_role_sound(role_id=role_id, role_name=role_name, file_path=str(target))
        await update.message.reply_text(
            f"Сохранено: роль {role_name} -> {target}",
            reply_markup=self._tg_keyboard(),
        )
        return ConversationHandler.END

    async def tg_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text("Операция отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    def _find_sound_for_member(self, member: discord.Member) -> Optional[str]:
        for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
            if role.name == "@everyone":
                continue
            path = self.storage.get_role_sound(role.id)
            if path:
                return path
        return None

    async def _play_sound(self, channel: discord.VoiceChannel, sound_path: str) -> None:
        if not Path(sound_path).exists():
            logger.warning("Файл звука не найден: %s", sound_path)
            return

        guild_id = channel.guild.id
        vc = self.voice_clients.get(guild_id)

        try:
            if vc is None or not vc.is_connected():
                vc = await channel.connect()
                self.voice_clients[guild_id] = vc
            elif vc.channel != channel:
                await vc.move_to(channel)

            if vc.is_playing():
                vc.stop()

            source = discord.FFmpegPCMAudio(sound_path)
            vc.play(source)

            while vc.is_playing():
                await asyncio.sleep(0.2)

            await vc.disconnect()
            self.voice_clients.pop(guild_id, None)

        except Exception as exc:
            logger.exception("Ошибка воспроизведения %s: %s", sound_path, exc)

    async def run(self) -> None:
        await self.telegram_app.initialize()
        await self.telegram_app.start()
        await self.telegram_app.updater.start_polling()
        logger.info("Telegram-бот запущен")

        try:
            await self.discord_bot.start(self.config.discord_token)
        finally:
            await self.telegram_app.updater.stop()
            await self.telegram_app.stop()
            await self.telegram_app.shutdown()


def main() -> None:
    config = load_config()
    app = DiscordTelegramSoundBot(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
