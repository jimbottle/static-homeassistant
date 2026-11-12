"""Checks on the conditions Static set that can be read from the repo itself.

Static agreed to an unofficial build on conditions. The ones about audio are
tested where the audio code lives; this file holds the ones a reader can see
without running anything: the project is called unofficial and credits Static.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")


def test_readme_title_says_unofficial() -> None:
    title = README.splitlines()[0]
    assert title.startswith("# ")
    assert "unofficial" in title.lower()


def test_readme_credits_and_links_static() -> None:
    assert "https://staticradio.app" in README


def test_readme_disclaims_endorsement() -> None:
    text = " ".join(README.split())
    assert "not affiliated with, endorsed by or supported by Static" in text
