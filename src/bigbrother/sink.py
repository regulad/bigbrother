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

import zlib
from asyncio import AbstractEventLoop, run_coroutine_threadsafe, Event
from concurrent.futures import ThreadPoolExecutor, wait, Future
from datetime import datetime
from functools import partial
from io import BytesIO
from types import SimpleNamespace
from typing import cast

from discord import VoiceClient, MISSING
from discord.sinks import OGGSink, Filters, AudioData
from discord.types.snowflake import Snowflake
from sqlalchemy import insert, update, select, Insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection

from .sql import Sessions, LeaveReason, Users


class BigBrotherSink(OGGSink):
    audio_data: dict[Snowflake, AudioData]
    vc: VoiceClient | None  # type: ignore  # Sink is wrong

    def __init__(
        self,
        sa_engine: AsyncEngine,
        loop: AbstractEventLoop,
        max_file_len: int | None | MISSING = MISSING,
        *,
        filters=None,  # type: ignore  # unknown
    ) -> None:
        super().__init__(filters=filters)

        self._sa_engine = sa_engine
        self._loop = loop
        self._max_file_len = max_file_len

        self.cleanup_event = Event()  # signature is wrong in 3.11.2, no loop kwarg

        self._can_listen_user_cache: dict[Snowflake, bool] = {}
        self._sessions: dict[Snowflake, int] = {}  # {member: session_id}

    def init(self, vc: VoiceClient) -> None:  # type: ignore  # called under listen; types are WRONG
        super().init(vc)
        if self._max_file_len is MISSING:
            bitrate_kbps = getattr(vc.channel, "bitrate", 64000)  # default 64kb
            self._max_file_len = bitrate_kbps * 30  # 30 seconds of audio in bytes

    @Filters.container
    def write(self, data: bytes, user: Snowflake) -> None:
        """
        Writes data to the sink.
        :param data: PCM audio data
        :param user: The user ID who sent the data.
        :return: None
        """
        assert self._max_file_len is not MISSING, "Max file length is missing!"

        voice_client = self.vc
        if voice_client is None:
            return  # Something didn't initialize correctly.

        if user not in self._can_listen_user_cache:

            async def check_can_listen() -> bool:
                async with self._sa_engine.begin() as conn:  # type: AsyncConnection
                    stmt: Select = select(Users.can_listen).where(Users.user_id == user)  # type: ignore
                    result = await conn.execute(stmt)

                    if result.rowcount == 0:  # type: ignore  # also uses a special decorator
                        # The user is not in the database, so we'll assume they can listen.
                        ins_stmt: Insert = insert(Users).values(user_id=user, can_listen=True)
                        await conn.execute(ins_stmt)
                        await conn.commit()
                        # We can listen to the user, since it's not in the database and therefore cannot be disallowed.
                        return True
                    else:
                        return cast(bool, result.scalar_one())

            can_listen = run_coroutine_threadsafe(check_can_listen(), self._loop).result()
            self._can_listen_user_cache[user] = can_listen

        can_listen = self._can_listen_user_cache[user]

        if not can_listen:
            return  # Privacy settings forbid us from recording this user.

        if user not in self.audio_data:
            bytesio = BytesIO()  # This is a file-like object that holds PCM audio frames
            self.audio_data[user] = AudioData(bytesio)

        if user not in self._sessions:
            # This is a new session, so we need to write the initial row to the database.
            now = datetime.utcnow()  # the PostgreSQL database is not timezone aware

            async def open_session() -> int:
                async with self._sa_engine.begin() as conn:  # type: AsyncConnection
                    stmt: Insert = insert(Sessions).values(
                        user_id=user,
                        channel_id=voice_client.channel.id,  # type: ignore  # ?????? I guess since it's in a function
                        started_at=now,
                    )
                    result = await conn.execute(stmt)
                    return cast(int, result.inserted_primary_key[0])

            session_id = run_coroutine_threadsafe(open_session(), self._loop).result()

            self._sessions[user] = session_id

        adata = self.audio_data[user]

        if self._max_file_len is not None and adata.file.tell() >= self._max_file_len:
            # The file is too long, so we need to split it into multiple files.
            # We'll do this by creating a new session.
            # We'll also need to write the old session to the database.

            self.cleanup_one(user, reason=LeaveReason.CONTINUED)
            self.write(data, user)  # Recurse to write the data to the new session.
            return

        # All good. Proceed!
        adata.write(data)

    def cleanup(self, *, reason: LeaveReason = LeaveReason.BOT_DISCONNECTED) -> None:
        """
        Cleans up all sessions & processes them. This will always be called by a worker thread.
        :param reason: a LeaveReason for this cleanup.
        :return: None, always.
        """
        self.finished = True
        with ThreadPoolExecutor(thread_name_prefix="CleanupExecutor") as executor:
            cleanup_futures: list[Future] = []
            for user in set(self.audio_data.keys()):
                cleanup_futures.append(executor.submit(partial(self.cleanup_one, user, reason=reason)))
            wait(cleanup_futures)
        self.cleanup_event.set()

    def cleanup_one(self, user: Snowflake, *, reason: LeaveReason = LeaveReason.NATURAL) -> int | None:
        """
        Cleans up a single user's session & processes it.
        This should be run from the Asyncio event loop's executor.
        :param reason: The LeaveReason for this cleanup.
        :param user: The user to clean up.
        :return: The session ID of the session that was cleaned up, or None if there was no session to clean up.
        """
        audio_data = self.audio_data.pop(user, None)

        if audio_data is None:
            # No audio data for this user.
            return None

        session_id = self._sessions.pop(user, None)

        # Let's do some work!

        audio_data.cleanup()

        # Dumbass format_audio only works when recording is False, so we need to fake something for it.
        thingamabob = SimpleNamespace()
        thingamabob.encoding = "ogg"
        thingamabob.vc = SimpleNamespace()
        thingamabob.vc.recording = False

        OGGSink.format_audio(thingamabob, audio_data)  # type: ignore

        fp = audio_data.file

        del audio_data  # Keep the memory usage down, still.

        # Send it!
        # Unfortunately, SQLAlchemy doesn't support file-like objects, so we need to read the data into memory.
        # We also compress the data to save space on the database.

        # fp.seek(0)  # done by format_audio
        uncompressed = fp.read()

        del fp  # Keep the memory usage down, still.

        data = zlib.compress(uncompressed)

        del uncompressed  # Keep the memory usage down.

        async def commit_session() -> None:
            async with self._sa_engine.begin() as conn:  # type: AsyncConnection
                stmt = (
                    update(Sessions)
                    .values(
                        ended_at=datetime.utcnow(),
                        data=data,
                        leave_reason=reason,
                    )
                    .where(Sessions.id == session_id)  # type: ignore
                )
                await conn.execute(stmt)
                await conn.commit()

        run_coroutine_threadsafe(commit_session(), self._loop).result()

        del data  # Don't wait for the garbage collector! This may invoke it on CPython.

        return session_id


__all__ = ("BigBrotherSink",)
