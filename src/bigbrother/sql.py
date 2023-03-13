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

import typing
from enum import Enum, auto
from typing import TypeAlias

from sqlalchemy import (
    MetaData,
    Column,
    Integer,
    BigInteger,
    DateTime,
    LargeBinary,
    ForeignKey,
    Boolean,
    Enum as EnumType,
)
from sqlalchemy.orm import declarative_base

# AioPG only supports sqlalchemy < 2.0.0, so we need to use the old API
metadata = MetaData()
Base: TypeAlias = declarative_base(metadata=metadata)  # type: ignore


class LeaveReason(Enum):
    """
    The reason a user left a voice channel.
    """

    # The user left the channel naturally.
    # Note: Discord doesn't tell us if someone got kicked or just disconnected, so we can't tell the difference.
    # This would also be written if the session was split into multiple sessions due to length limits, and this is the
    # last session.
    NATURAL = auto()
    # The bot was disconnected from the voice channel, forcing the session to end unnaturally.
    BOT_DISCONNECTED = auto()
    # The session was cut smaller sessions due to length limits, and this is not the last session.
    CONTINUED = auto()


class AuditEvent(Enum):
    """
    The event that occured.
    """

    USER_CHANGED_PRIVACY_SETTING_ENABLED_LISTENING = auto()
    USER_CHANGED_PRIVACY_SETTING_DISABLED_LISTENING = auto()

    GUILD_CHANNEL_PRIVACY_SETTING_ENABLED_LISTENING = auto()
    GUILD_CHANNEL_PRIVACY_SETTING_DISABLED_LISTENING = auto()


@typing.no_type_check  # mypy bug! the columns aren't being recognized as being in the class
class Sessions(Base):
    """
    A session is a recording of a user's voice data in a voice channel.
    A session may be "unfinished" if:
        - ended_at is None
        - leave_reason is None
        - data is None
    If data is None BUT ended_at and leave_reason are not None, then the session is "finished" but the data is missing.
    """

    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False, unique=True)

    channel_id = Column(BigInteger, ForeignKey("voice_channels.channel_id"), nullable=False)

    user_id = Column(BigInteger, ForeignKey("users.user_id"), nullable=False)

    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)

    data = Column(LargeBinary, nullable=True)  # ZLIB compressed Opus data

    leave_reason = Column(EnumType(LeaveReason), nullable=True)


@typing.no_type_check
class Users(Base):
    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True, nullable=False, unique=True)

    can_listen = Column(Boolean, nullable=False, default=True)

    audit_event_id = Column(None, ForeignKey("audit_log.id"), nullable=True)


@typing.no_type_check
class VoiceChannels(Base):
    __tablename__ = "voice_channels"

    channel_id = Column(BigInteger, primary_key=True, nullable=False, unique=True)

    can_listen = Column(Boolean, nullable=False, default=True)
    autoconnect = Column(Boolean, nullable=False, default=True)

    audit_event_id = Column(None, ForeignKey("audit_log.id"), nullable=True)


@typing.no_type_check
class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False, unique=True)

    responsible_user_id = Column(BigInteger, nullable=False)
    changed_at = Column(DateTime, nullable=False)

    # Guild ID, may be null if the event was a user privacy setting change.
    scope = Column(BigInteger, nullable=True)

    event_type = Column(EnumType(AuditEvent), nullable=False)


__all__ = (
    "metadata",
    "Base",
    # Enums
    "LeaveReason",
    "AuditEvent",
    # Tables
    "VoiceChannels",
    "AuditLog",
    "Sessions",
    "Users",
)
