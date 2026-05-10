from setuptools import setup, find_packages

setup(
    name="lyrics-finder",
    version="1.0.0",
    description="Search Musixmatch for lyrics and embed them into MP3/M4A audio files",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "mutagen>=1.47.0",
        "requests>=2.28.0",
        "flask>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            "lyrics-finder=lyrics_finder.cli:main",
            "lyrics-finder-ui=webui:main",
            "ipod-manager=ipod_manager.cli:main",
        ],
    },
)
