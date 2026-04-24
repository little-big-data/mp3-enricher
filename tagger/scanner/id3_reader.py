# tagger/scanner/id3_reader.py
from pathlib import Path
from typing import Any

import structlog
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

log = structlog.get_logger(__name__)


def read_id3_tags(file_path: Path) -> dict[str, Any]:
    """
    Reads ID3 tags from an MP3 file using mutagen.

    Args:
        file_path: The path to the MP3 file.

    Returns:
        A dictionary containing relevant ID3 tag information, including:
        'title', 'artist', 'album', 'track_number', 'year', 'album_artist',
        'composer', 'bpm', 'genre', 'disc_number'.
        Returns an empty dictionary if tags cannot be read or the file is invalid.
    """
    tags: dict[str, Any] = {}
    try:
        audio = MP3(file_path, ID3=ID3)
        if audio.tags:
            # Title
            if "TIT2" in audio.tags:
                tags["title"] = audio.tags["TIT2"].text[0] if audio.tags["TIT2"].text else None
            # Artist (Lead performer/Soloist)
            if "TPE1" in audio.tags:
                tags["artist"] = audio.tags["TPE1"].text[0] if audio.tags["TPE1"].text else None
            # Album Artist
            if "TPE2" in audio.tags:
                text = audio.tags["TPE2"].text
                tags["album_artist"] = text[0] if text else None
            # Album
            if "TALB" in audio.tags:
                tags["album"] = audio.tags["TALB"].text[0] if audio.tags["TALB"].text else None
            # Track Number
            if "TRCK" in audio.tags:
                track_num_str = audio.tags["TRCK"].text[0] if audio.tags["TRCK"].text else None
                if track_num_str:
                    try:
                        if "/" in track_num_str:
                            tags["track_number"] = int(track_num_str.split("/")[0])
                        else:
                            tags["track_number"] = int(track_num_str)
                    except ValueError:
                        log.warning(
                            "id3_reader.invalid_track_format",
                            file_path=str(file_path),
                            track_str=track_num_str,
                        )
                        tags["track_number"] = None
            # Year
            if "TDRC" in audio.tags:  # Year (ID3v2.4)
                val = audio.tags["TDRC"].text[0] if audio.tags["TDRC"].text else None
                if val:
                    try:
                        # Convert mutagen ID3TimeStamp to string and take the year part
                        year_str = str(val)[:4]
                        tags["year"] = int(year_str)
                    except (ValueError, TypeError):
                        log.warning(
                            "id3_reader.invalid_year_format",
                            file_path=str(file_path),
                            year_val=str(val),
                        )
                        tags["year"] = None
            elif "TYER" in audio.tags:  # Year (ID3v2.3) - Fallback
                val = audio.tags["TYER"].text[0] if audio.tags["TYER"].text else None
                if val:
                    try:
                        year_str = str(val)[:4]
                        tags["year"] = int(year_str)
                    except (ValueError, TypeError):
                        log.warning(
                            "id3_reader.invalid_year_format",
                            file_path=str(file_path),
                            year_val=str(val),
                        )
                        tags["year"] = None
            # BPM
            if "TBPM" in audio.tags:
                bpm_str = audio.tags["TBPM"].text[0] if audio.tags["TBPM"].text else None
                if bpm_str:
                    try:
                        tags["bpm"] = int(bpm_str)
                    except ValueError:
                        log.warning(
                            "id3_reader.invalid_bpm_format",
                            file_path=str(file_path),
                            bpm_str=bpm_str,
                        )
                        tags["bpm"] = None
            # Genre
            if "TCON" in audio.tags:
                tags["genre"] = audio.tags["TCON"].text[0] if audio.tags["TCON"].text else None
            # Disc Number
            if "TPOS" in audio.tags:
                disc_num_str = audio.tags["TPOS"].text[0] if audio.tags["TPOS"].text else None
                if disc_num_str:
                    try:
                        if "/" in disc_num_str:
                            tags["disc_number"] = int(disc_num_str.split("/")[0])
                        else:
                            tags["disc_number"] = int(disc_num_str)
                    except ValueError:
                        log.warning(
                            "id3_reader.invalid_disc_number_format",
                            file_path=str(file_path),
                            disc_str=disc_num_str,
                        )
                        tags["disc_number"] = None
            # Composer (added for completeness, though not explicitly in brief for scan phase)
            if "TCOM" in audio.tags:
                tags["composer"] = audio.tags["TCOM"].text[0] if audio.tags["TCOM"].text else None

            # Other tags like comment (COMM), lyrics (USLT) can be parsed if needed,
            # but are often not critical for the initial scan phase.

        return tags

    except Exception as e:
        log.error(
            "id3_reader.failed_to_read_tags", file_path=str(file_path), error=str(e), exc_info=True
        )
        return {}
