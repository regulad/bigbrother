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

import datetime
from typing import List, Dict, Optional

shorthands: List[str] = [
    "y",
    "mo",
    "w",
    "d",
    "h",
    "m",
    "s",
]


# https://github.com/regulad/PepperCord/blob/c1614222497582fe2419f59b07128a7ab1fe1262/src/utils/converters.py
def shorthand_to_timedelta(shorthand: str) -> datetime.timedelta:
    """Shorthand:
    y: Years
    mo: Months
    w: Weeks
    d: Days
    h: Hours
    m: Minutes
    s: Seconds"""

    # Checks if a known unit of time is present in the shorthand.
    for possible_shorthand in shorthands:
        if possible_shorthand in shorthand:
            break
    else:
        raise TypeError("No unit of time in shorthand.")

    # Splits the shorthand up into smaller pieces.
    units: Dict[str, Optional[float]] = {
        "y": None,
        "mo": None,
        "w": None,
        "d": None,
        "h": None,
        "m": None,
        "s": None,
    }
    for possible_shorthand in shorthands:
        if len(shorthand) == 0:
            break
        if shorthand.find(possible_shorthand) != -1:
            index: int = shorthand.find(possible_shorthand)
            units[possible_shorthand] = float(shorthand[:index])
            shorthand = shorthand[index + 1 :]

    days: float = (units["y"] * 365 if units["y"] is not None else 0) + (
        units["mo"] * 30 if units["mo"] is not None else 0
    )

    return datetime.timedelta(
        weeks=units["w"] or 0,
        days=days + units["d"] if units["d"] is not None else days,  # Kinda stupid!
        hours=units["h"] or 0,
        minutes=units["m"] or 0,
        seconds=units["s"] or 0,
    )


__all__ = ("shorthand_to_timedelta",)
