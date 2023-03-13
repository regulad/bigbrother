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
# from __future__ import annotations  # discord.py needs em
import zlib
from asyncio import Future
from datetime import timedelta, datetime
from functools import partial
from logging import getLogger
from subprocess import Popen
from typing import cast, Sequence, Any

import ffmpeg
from discord import Bot, VoiceChannel, VoiceClient, VoiceState, Member, command, File
from discord.ext.bridge import BridgeApplicationContext
from discord.ext.commands import Cog
from discord.sinks import RecordingException
from sqlalchemy import select, insert, Insert, Select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection

from .file_management import FileManager, ScratchFile
from .peppercord_audio import CustomVoiceClient
from .sink import BigBrotherSink
from .sql import VoiceChannels, LeaveReason, Sessions
from .utils import shorthand_to_timedelta

logger = getLogger(__name__)


async def listen_callback(*args) -> None:
    logger.debug(f"Recording finished with args: {args}")


async def close_voice_client(voice_client: VoiceClient) -> bool:
    """
    Close a voice client, and clean up any associated resources.
    :param voice_client: The voice client to close.
    :return: True if successful, False otherwise.
    """

    try:
        voice_client.stop_recording()
    except RecordingException:
        return False
    else:
        sink = cast("BigBrotherSink", voice_client.sink)
        await sink.cleanup_event.wait()
    finally:
        await voice_client.disconnect(force=True)
        return True


class BigBrother(Cog):
    """
    BigBrother worker cog.
    """

    def __init__(self, bot: Bot, sa_engine: AsyncEngine, fm: FileManager) -> None:
        self.bot = bot
        self._sa_engine = sa_engine
        self._file_manager = fm

    async def connect_and_listen(self, voice_channel: VoiceChannel, *, bypass: bool = False) -> VoiceClient | None:
        """
        Connect to a voice channel, and initialize a Sink to listen to it.
        :param voice_channel: The voice channel to listen to.
        :param bypass: Whether to bypass the voice channel's privacy settings.
        :return: A VoiceClient object, or None if the bot is unable to connect to the voice channel.
        """
        if voice_channel.guild.voice_client is not None:
            # We're already connected to a voice channel in this guild.
            return None

        async with self._sa_engine.begin() as conn:  # type: AsyncConnection
            stmt = select(VoiceChannels.can_listen).where(VoiceChannels.channel_id == voice_channel.id)  # type: ignore
            result = await conn.execute(stmt)

            if result.rowcount == 0:  # type: ignore  # also uses a special decorator
                # We need to add the voice channel to the database.
                ins_stmt: Insert = insert(VoiceChannels).values(channel_id=voice_channel.id, can_listen=True)
                await conn.execute(ins_stmt)
                await conn.commit()
                # We can listen to the voice channel, since it's not in the database and therefore cannot be disallowed.
                pass
            else:
                # The voice channel is in the database, so we can check if it's allowed to be listened to.
                can_listen = result.scalar_one()
                if not can_listen and not bypass:
                    # The voice channel is not allowed to be listened to.
                    return None

        client: VoiceClient = await voice_channel.connect(cls=CustomVoiceClient)

        sink = BigBrotherSink(self._sa_engine, self.bot.loop)

        client.start_recording(
            sink,
            listen_callback,
            *(),
        )

        return client

    @Cog.listener("on_voice_state_update")
    async def on_people_joining(self, member: Member, before: VoiceState, after: VoiceState) -> None:
        if member is member.guild.me:
            # Hey, that's me!
            return

        if after.channel is None:
            # The member left a voice channel. Nothing of interest happened.
            return

        if not isinstance(after.channel, VoiceChannel):
            # The member joined a stage channel. Nothing of interest happened.
            return

        guild_vc = cast("VoiceClient | None", member.guild.voice_client)

        if guild_vc is not None:
            # We're already connected to a voice channel in this guild.
            return

        # Let's check if we can autoconnect to this voice channel.

        can_autoconnect: bool = VoiceChannels.autoconnect.default.arg  # type: ignore

        async with self._sa_engine.begin() as conn:  # type: AsyncConnection
            stmt = select(VoiceChannels.autoconnect).where(VoiceChannels.channel_id == after.channel.id)  # type: ignore
            result = await conn.execute(stmt)

            if result.rowcount > 0:  # type: ignore  # also uses a special decorator
                can_autoconnect = result.scalar_one_or_none() or can_autoconnect

        if can_autoconnect:
            await self.connect_and_listen(after.channel)

    @Cog.listener("on_voice_state_update")
    async def on_natural_leave(self, member: Member, before: VoiceState, after: VoiceState) -> None:
        guild_vc = cast("VoiceClient | None", member.guild.voice_client)

        if member is member.guild.me:
            # Hey, that's me!
            return

        if guild_vc is None:
            # We're not connected to a voice channel in this guild.
            return

        if before.channel is None:
            # The member joined a voice channel. Nothing of interest happened.
            return

        if after.channel is None or after.channel is not guild_vc.channel:
            # The member left a voice channel. This is what we care about!
            sink = cast("BigBrotherSink", guild_vc.sink)
            await self.bot.loop.run_in_executor(None, partial(sink.cleanup_one, member.id, reason=LeaveReason.NATURAL))

    @Cog.listener("on_voice_state_update")
    async def on_left_alone(self, member: Member, before: VoiceState, after: VoiceState) -> None:
        guild_vc = cast("VoiceClient | None", member.guild.voice_client)

        if member is member.guild.me:
            # Hey, that's me!
            return

        if guild_vc is None:
            # We're not connected to a voice channel in this guild.
            return

        if before.channel is None:
            # The member joined a voice channel. Nothing of interest happened.
            return

        if before.channel != guild_vc.channel:
            # The member is not in the voice channel we're connected to.
            return

        if len(before.channel.members) <= 1:
            # The member left the voice channel, and we're the only one left.
            await close_voice_client(guild_vc)

    @command()
    async def listen(self, ctx: BridgeApplicationContext) -> None:
        """
        Start listening to a voice channel.
        """
        await ctx.defer()

        if not isinstance(ctx.author, Member):
            await ctx.respond("You must be a member of a server to use this command!")
            return

        if ctx.author.voice is None:
            await ctx.respond("You must be in a voice channel to use this command!")
            return

        author_voice_state: VoiceState = ctx.author.voice

        if not isinstance(author_voice_state.channel, VoiceChannel):
            await ctx.respond("You must be in a voice channel to use this command! Stage channels don't count!")
            return

        try:
            client = await self.connect_and_listen(author_voice_state.channel)
        except Exception as e:
            logger.exception(f"Error connecting to voice channel: {e}")
            await ctx.respond(f"Error connecting to voice channel: {e}")
            return
        else:
            if client is None:
                await ctx.respond(
                    "I couldn't listen to that voice channel! "
                    "This probably means the moderators have disabled my access."
                )
                return
            else:
                await ctx.respond("Listening!")
                return

    @command()
    async def stop(self, ctx: BridgeApplicationContext) -> None:
        """
        Stop listening to a voice channel.
        """

        if not isinstance(ctx.author, Member):
            await ctx.respond("You must be a member of a server to use this command!")
            return

        if ctx.author.voice is None:
            await ctx.respond("You must be in a voice channel to use this command!")
            return

        author_voice_state: VoiceState = ctx.author.voice
        guild_voice_client: VoiceClient | None = cast("VoiceClient | None", ctx.guild.voice_client)

        if guild_voice_client is None:
            await ctx.respond("I'm not listening to anything!")
            return

        if guild_voice_client.channel != author_voice_state.channel:
            await ctx.respond("I'm not listening to that channel!")
            return

        closed = await close_voice_client(guild_voice_client)

        if closed:
            await ctx.respond("Stopped listening!")
        else:
            await ctx.respond("I couldn't stop listening!")

    @command()
    async def holdup(self, ctx: BridgeApplicationContext, *, who: Member, last: str = "30s") -> None:
        """
        Recall what somebody just said. Last defaults to 30 seconds. (30s)
        """
        await ctx.defer()

        try:
            last_td: timedelta = shorthand_to_timedelta(last)
        except (ValueError, TypeError, OverflowError, KeyError):  # my code was not the cleanest back then
            await ctx.respond("Invalid time!")
            return

        if not isinstance(ctx.author, Member):
            await ctx.respond("You must be a member of a server to use this command!")
            return

        now = datetime.utcnow()
        starting_at = now - last_td

        if ctx.guild.voice_client is not None:
            # We need to sinch off the current session so we can recall what the user is saying.
            guild_vc = cast("VoiceClient", ctx.guild.voice_client)
            sink = cast("BigBrotherSink", guild_vc.sink)
            await ctx.bot.loop.run_in_executor(None, partial(sink.cleanup_one, who.id, reason=LeaveReason.CONTINUED))

        async with self._sa_engine.begin() as conn:  # type: AsyncConnection
            last_session_stmt: Select = (
                select(Sessions.started_at)
                .order_by(Sessions.started_at.desc())
                .where(Sessions.user_id == who.id)  # type: ignore
            )
            last_session_res = await conn.execute(last_session_stmt)
            last_session: datetime | None = last_session_res.scalar()

            if (
                last_session is not None and last_session < starting_at
            ):  # last session is older than the requested time
                starting_at = last_session

            sel_stmt: Select = (
                select(Sessions.data)
                .where(Sessions.user_id == who.id)  # type: ignore
                .where(Sessions.started_at >= starting_at)
                .where(Sessions.data != None)  # noqa
            )
            result = await conn.execute(sel_stmt)
            compressed_chunks: Sequence[bytes] = result.scalars().all()

        if not compressed_chunks:
            await ctx.respond("They didn't say anything!")
            return

        uncompressed_chunks: list[bytes] = []
        decompression_multithread_futures: list[Future[bytes]] = []

        for compressed_chunk in compressed_chunks:
            future = ctx.bot.loop.run_in_executor(None, zlib.decompress, compressed_chunk)
            decompression_multithread_futures.append(future)
        for future in decompression_multithread_futures:
            uncompressed_chunks.append(await future)

        del compressed_chunks  # save some memory

        async with self._file_manager.get_file(file_extension=".ogg") as output_file:
            ffmpeg_streams_concat: list[tuple[ScratchFile, Any]] = []  # "Any" is the ffmpeg stream

            for uncompressed_chunk in uncompressed_chunks:
                scratch = self._file_manager.get_file(file_extension=".ogg", initial_bytes=uncompressed_chunk)
                scratch.open("w")  # write the initial bytes
                stream = ffmpeg.input(str(scratch.path))
                ffmpeg_streams_concat.append((scratch, stream))

            del uncompressed_chunks  # trigger early gc maybe

            try:
                ffmpeg_process: Popen = (
                    ffmpeg.concat(*tuple(ffmpeg_stream for _, ffmpeg_stream in ffmpeg_streams_concat), v=0, a=1)
                    .output(str(output_file.path))
                    .run_async()
                )
                with ffmpeg_process:  # in case of anything happening with the executor
                    await ctx.bot.loop.run_in_executor(None, partial(ffmpeg_process.wait))
            finally:
                for scratch_file, _ in ffmpeg_streams_concat:
                    scratch_file.close()

            del ffmpeg_streams_concat  # early gc? pretty please?

            with output_file.open("r") as fp:
                fp.seek(0)

                discord_file = File(fp, filename=f"{who.id}.ogg")  # type: ignore  # also works with raw IO base

                await ctx.respond("Here's what they said:", file=discord_file)


__all__ = ("BigBrother",)
