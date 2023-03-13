# bigbrother listens to your discord voice chats and lets you recall the audio data
# Copyright (C) 2023 Parker Wahle
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations

import asyncio
import sys
from logging import DEBUG, INFO, StreamHandler, ERROR, basicConfig, getLogger, WARNING
from os import environ
from pathlib import Path
from urllib.parse import quote

from discord import Intents
from discord.ext.bridge import Bot
from dislog import DiscordWebhookHandler
from sqlalchemy.ext.asyncio import create_async_engine

from . import *

logger = getLogger(__name__)

# https://github.com/aio-libs/aiopg/issues/678
if sys.version_info >= (3, 8) and sys.platform.lower().startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore  # not present on POSIX


async def async_main() -> None:
    # We are debugging our program, not py-cord itself.
    getLogger("discord").setLevel(INFO if __debug__ else WARNING)

    loop = asyncio.get_event_loop()

    # py-cord doesn't include the discord.py logging magic, so we have to do it ourselves
    standard_handler = StreamHandler(sys.stdout)
    error_handler = StreamHandler(sys.stderr)

    standard_handler.addFilter(
        lambda record: record.levelno < ERROR
    )  # keep errors to stderr, alternative to ansi colors
    error_handler.setLevel(ERROR)

    basicConfig(
        format="%(asctime)s\t%(levelname)s\t%(name)s: %(message)s",
        level=DEBUG if __debug__ else INFO,
        handlers=[standard_handler, error_handler],
    )

    # Dislog
    dislog_url: str | None = environ.get("BIGBROTHER_DISCORD_WEBHOOK")

    if dislog_url is not None:
        logger.info("Discord Webhook provided, enabling Discord logging.")

        dislog_message: str | None = environ.get("BIGBROTHER_DISCORD_WEBHOOK_MESSAGE")

        handler = DiscordWebhookHandler(
            dislog_url,
            level=INFO,  # debug is just too much for discord to handle
            text_send_on_error=dislog_message,
            event_loop=loop,
        )
        getLogger().addHandler(handler)

    logger.debug("Logging configured successfully.")

    # Starting bot

    # Database argument preperation

    postgres_host = environ.get("BIGBROTHER_POSTGRES_HOST", "localhost")
    postgres_port = int(environ.get("BIGBROTHER_POSTGRES_PORT", "5432"))

    postgres_user = environ.get("BIGBROTHER_POSTGRES_USER", "bigbrother")
    postgres_password = environ.get("BIGBROTHER_POSTGRES_PASSWORD", "bigbrother")
    postgres_database = environ.get("BIGBROTHER_POSTGRES_DATABASE", "bigbrother")

    postgres_user_escaped = quote(postgres_user)
    postgres_password_escaped = quote(postgres_password)

    postgres_uri = f"postgresql+asyncpg://{postgres_user_escaped}:{postgres_password_escaped}@{postgres_host}:{postgres_port}/{postgres_database}"

    sa_engine = create_async_engine(postgres_uri)
    logger.debug("Database connection established.")

    logger.debug("Creating tables...")
    metadata.bind = sa_engine  # type: ignore  # noqa  # marked with a special decorator

    async with sa_engine.begin() as conn:
        if __debug__:
            # Only drop tables in debug mode. We wouldn't want our production data to suddenly go missing.
            # This will require us to manually drop tables in production or ALTER them to add new columns.
            await conn.run_sync(metadata.drop_all)

        await conn.run_sync(metadata.create_all)

    logger.debug("Tables created successfully.")

    # Scratch folder
    folder_path_str = environ.get("BIGBROTHER_SCRATCH_FOLDER", "./scratch")
    folder_path = Path(folder_path_str).absolute().resolve()

    folder_path.mkdir(exist_ok=True, parents=True)

    fm = FileManager(folder_path)

    # We need to enable the members intent to get the members in a voice channel
    intents = Intents.default()
    intents.members = True  # type: ignore  # noqa  # marked with a special decorator

    # Debug guilds are guilds that have application commands written into them for testing purposes.
    # They are not global and take no time to propagate.
    if "BIGBROTHER_DEBUG_GUILDS" in environ:
        logger.info("Debug guilds provided, enabling debug mode.")
        debug_guilds = [int(guild_id) for guild_id in environ["BIGBROTHER_DEBUG_GUILDS"].split(",")]
    else:
        debug_guilds = None

    # Bot argument preparation
    bot = Bot(
        intents=intents,
        command_prefix=";",
        loop=loop,
        debug_guilds=debug_guilds,
        description="bigbrother listens to your discord voice chats and lets you recall the audio data",
    )
    bot.add_cog(BigBrother(bot, sa_engine, fm))

    # Bot startup
    logger.info("Starting bot...")
    bot_token = environ["BIGBROTHER_BOT_TOKEN"]
    try:
        await bot.start(bot_token)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
        await bot.close()
    finally:
        if not bot.is_closed():
            await bot.close()
        await sa_engine.dispose()
        fm.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
