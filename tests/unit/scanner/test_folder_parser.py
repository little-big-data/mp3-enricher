from pathlib import Path

import pytest

from tagger.scanner.folder_parser import parse_folder_names


@pytest.mark.parametrize(
    ("folder_path", "expected_artist", "expected_album"),
    [
        # Standard Artist - Album format
        (Path("The Beatles - Abbey Road"), "The Beatles", "Abbey Road"),
        (Path("Queen - A Night at the Opera"), "Queen", "A Night at the Opera"),
        # Variations with different separators
        (Path("Led Zeppelin / Led Zeppelin IV"), "Led Zeppelin", "Led Zeppelin IV"),
        (
            Path("Pink Floyd - The Dark Side of the Moon (Remastered)"),
            "Pink Floyd",
            "The Dark Side of the Moon (Remastered)",
        ),
        (Path("Artist_Album"), "Artist", "Album"),
        (Path("Artist | Album"), "Artist", "Album"),
        # Artist only
        (Path("SoloArtist"), None, "SoloArtist"),
        # Album only (less common, but for completeness)
        (Path("Album Without Artist"), None, "Album Without Artist"),
        # Complex names
        (
            Path("Artist Name & Co. - Album Title [Special Edition]"),
            "Artist Name & Co.",
            "Album Title [Special Edition]",
        ),
        # Paths with multiple levels
        (Path("Some/Path/Artist - Album"), "Artist", "Album"),
        # Empty folder name (should not happen, but for robustness)
        (Path(""), None, None),
        # Folder name with only hyphens (edge case)
        (Path(" - "), None, None),
        # Artist and album with special characters
        (Path("!!! Band !!! - ### Album ###"), "!!! Band !!!", "### Album ###"),
    ],
)
def test_parse_folder_names_various_formats(
    folder_path: Path, expected_artist: str | None, expected_album: str | None
) -> None:
    """
    Tests the folder_parser.py function with various folder naming conventions.
    """
    result = parse_folder_names(folder_path)

    assert result.get("artist_guess") == expected_artist
    assert result.get("album_guess") == expected_album


@pytest.mark.parametrize(
    ("folder_path", "expected_artist", "expected_album"),
    [
        # Artist and album extracted from a more complex path structure
        (Path("C:/Music/2020s/2023/Artist Name - Album Title"), "Artist Name", "Album Title"),
        (
            Path("/home/user/mp3s/Jazz/Miles Davis/Kind of Blue"),
            "Miles Davis",
            "Kind of Blue",
        ),
    ],
)
def test_parse_folder_names_complex_paths(
    folder_path: Path, expected_artist: str | None, expected_album: str | None
) -> None:
    """
    Tests parsing from deeper directory structures, ensuring only the
    immediate parent is considered if no separator is found in the folder name.
    """
    result = parse_folder_names(folder_path)
    assert result.get("artist_guess") == expected_artist
    assert result.get("album_guess") == expected_album
