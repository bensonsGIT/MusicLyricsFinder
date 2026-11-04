"""Tests for vibe-tagger: tags, clustering, the CLI flow and audio features."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from mutagen.id3 import ID3, TIT1

from ipod_manager.itunesdb import Track, iTunesDB
from lyrics_finder.metadata import read_metadata
from tests.test_metadata import _make_m4a, _make_mp3
from vibe_tagger import cli
from vibe_tagger.features import FEATURES
from vibe_tagger.tags import is_vibe, label, read_grouping, write_grouping


def _vector(energy: float, percussive: float) -> list[float]:
    values = [0.0] * len(FEATURES)
    values[FEATURES.index("energy")] = energy
    values[FEATURES.index("percussiveness")] = percussive
    return values


# ------------------------------------------------------------------ tags

@pytest.mark.parametrize("make, name", [(_make_mp3, "a.mp3"), (_make_m4a, "a.m4a")])
def test_grouping_roundtrip_and_clear(tmp_path, make, name):
    path = tmp_path / name
    make(path, title="Song")
    assert read_grouping(path) == ""
    write_grouping(path, "vibe:07")
    assert read_grouping(path) == "vibe:07"
    assert read_metadata(path)["grouping"] == "vibe:07"
    assert read_metadata(path)["title"] == "Song"
    write_grouping(path, "")
    assert read_grouping(path) == ""


def test_mp3_grouping_uses_grp1_not_work_frame(tmp_path):
    path = tmp_path / "a.mp3"
    _make_mp3(path)
    tags = ID3(path)
    tags.add(TIT1(encoding=3, text=["Symphony No. 5"]))
    tags.save(path)
    write_grouping(path, "vibe:01")
    tags = ID3(path)
    assert str(tags["GRP1"]) == "vibe:01"
    assert str(tags["TIT1"]) == "Symphony No. 5"


def test_labels():
    assert label(3) == "vibe:03"
    assert is_vibe("vibe:40")
    assert not is_vibe("Live recordings")


# ------------------------------------------------------------------ itunesdb

def test_itunesdb_keeps_grouping(tmp_path):
    db = iTunesDB()
    db.tracks.append(Track(title="T", grouping="vibe:12", file_type="MP3 ",
                           ipod_path=":iPod_Control:Music:F00:0001.mp3"))
    db.write(tmp_path / "iTunesDB")
    assert iTunesDB.read(tmp_path / "iTunesDB").tracks[0].grouping == "vibe:12"


# ------------------------------------------------------------------ cluster

def test_assign_separates_groups_and_numbers_calm_first():
    from vibe_tagger.cluster import assign

    calm = [_vector(-40 + i * 0.1, 0.5) for i in range(5)]
    loud = [_vector(-8 + i * 0.1, 3.0) for i in range(5)]
    vibes = assign(calm + loud, clusters=2)
    assert set(vibes[:5]) == {1}
    assert set(vibes[5:]) == {2}


def test_assign_caps_clusters_at_song_count():
    from vibe_tagger.cluster import assign

    assert sorted(assign([_vector(-30, 1), _vector(-10, 2)], clusters=40)) == [1, 2]


# ------------------------------------------------------------------ cli

@pytest.fixture
def library(tmp_path, monkeypatch):
    """Three calm and three loud songs with faked analysis."""
    pytest.importorskip("sklearn")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Analyse in-process so the fake below applies, without lowering the test
    # runner's own priority.
    monkeypatch.setattr(cli, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(cli, "_worker_start", lambda: None)
    for name in cli._SINGLE_THREAD:
        monkeypatch.delenv(name, raising=False)
    calls = []

    def fake(path):
        calls.append(path)
        loud = "loud" in path
        return _vector(-8 if loud else -40, 3.0 if loud else 0.5)

    monkeypatch.setattr(cli, "_analyze", fake)
    music = tmp_path / "music"
    music.mkdir()
    for i in range(3):
        _make_mp3(music / f"calm{i}.mp3", title=f"Calm {i}", artist="A")
        _make_m4a(music / f"loud{i}.m4a", title=f"Loud {i}", artist="B")
    return music, calls


def test_cli_writes_vibes(library):
    music, _ = library
    assert cli.main([str(music), "--clusters", "2", "--jobs", "1"]) == 0
    assert {read_grouping(p) for p in music.glob("calm*")} == {"vibe:01"}
    assert {read_grouping(p) for p in music.glob("loud*")} == {"vibe:02"}


def test_cli_dry_run_writes_nothing(library):
    music, _ = library
    assert cli.main([str(music), "--clusters", "2", "--dry-run"]) == 0
    assert all(read_grouping(p) == "" for p in music.iterdir())


def test_cli_keeps_user_grouping_unless_forced(library):
    music, _ = library
    write_grouping(music / "calm0.mp3", "Wedding playlist")
    cli.main([str(music), "--clusters", "2"])
    assert read_grouping(music / "calm0.mp3") == "Wedding playlist"
    cli.main([str(music), "--clusters", "2", "--force"])
    assert read_grouping(music / "calm0.mp3") == "vibe:01"


def test_cli_reuses_cache_after_tagging(library):
    music, calls = library
    cli.main([str(music), "--clusters", "2"])
    first = len(calls)
    cli.main([str(music), "--clusters", "2"])
    assert first == 6 and len(calls) == first


def test_cli_clear_removes_only_vibes(library):
    music, _ = library
    cli.main([str(music), "--clusters", "2"])
    write_grouping(music / "calm1.mp3", "Keep me")
    assert cli.main([str(music), "--clear"]) == 0
    assert read_grouping(music / "calm1.mp3") == "Keep me"
    assert read_grouping(music / "loud0.m4a") == ""


# ------------------------------------------------------------------ features

def test_features_tell_quiet_tone_from_loud_beat(tmp_path):
    np = pytest.importorskip("numpy")
    soundfile = pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    from vibe_tagger.features import SAMPLE_RATE, analyze

    t = np.arange(SAMPLE_RATE * 8) / SAMPLE_RATE
    calm = 0.05 * np.sin(2 * np.pi * 220 * t)
    beat = np.random.default_rng(0).normal(0, 0.3, t.size) * (np.sin(2 * np.pi * 2 * t) > 0.9)
    soundfile.write(tmp_path / "calm.wav", calm, SAMPLE_RATE)
    soundfile.write(tmp_path / "beat.wav", beat, SAMPLE_RATE)

    quiet, loud = analyze(tmp_path / "calm.wav"), analyze(tmp_path / "beat.wav")
    assert len(quiet) == len(FEATURES)
    assert all(np.isfinite(quiet)) and all(np.isfinite(loud))
    for feature in ("noisiness", "percussiveness", "dynamics", "drums", "steadiness"):
        index = FEATURES.index(feature)
        assert loud[index] > quiet[index], feature
    # A pure tone is the sparsest possible spectrum.
    assert quiet[FEATURES.index("contrast")] > loud[FEATURES.index("contrast")]


def test_features_measure_bass_and_beat_steadiness(tmp_path):
    np = pytest.importorskip("numpy")
    soundfile = pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    from vibe_tagger.features import SAMPLE_RATE, analyze

    t = np.arange(SAMPLE_RATE * 12) / SAMPLE_RATE
    soundfile.write(tmp_path / "low.wav", 0.3 * np.sin(2 * np.pi * 60 * t), SAMPLE_RATE)
    soundfile.write(tmp_path / "high.wav", 0.3 * np.sin(2 * np.pi * 2000 * t), SAMPLE_RATE)
    low, high = analyze(tmp_path / "low.wav"), analyze(tmp_path / "high.wav")
    assert low[FEATURES.index("bass")] > 0.9 > 0.1 > high[FEATURES.index("bass")]

    rng = np.random.default_rng(1)
    click = rng.normal(0, 0.5, 400)

    def clicks(times):
        audio = np.zeros(t.size)
        for start in (times * SAMPLE_RATE).astype(int):
            audio[start:start + click.size] += click[:max(0, min(click.size, t.size - start))]
        return audio

    soundfile.write(tmp_path / "steady.wav", clicks(np.arange(0, 11.5, 0.5)), SAMPLE_RATE)
    soundfile.write(tmp_path / "loose.wav", clicks(np.sort(rng.uniform(0, 11.5, 23))), SAMPLE_RATE)
    steady, loose = analyze(tmp_path / "steady.wav"), analyze(tmp_path / "loose.wav")
    assert steady[FEATURES.index("steadiness")] > loose[FEATURES.index("steadiness")] + 0.2


def test_push_tags_to_db_carries_grouping(tmp_path):
    from ipod_manager.manager import IpodManager
    from tests.test_ipod_manager import _stock_mount

    mount = _stock_mount(tmp_path)
    song = tmp_path / "iPod_Control" / "Music" / "F00" / "0001.mp3"
    _make_mp3(song, title="Song")
    write_grouping(song, "vibe:05")
    db = iTunesDB()
    db.tracks.append(Track(title="Song", ipod_path=":iPod_Control:Music:F00:0001.mp3"))
    db.write(mount.itunesdb_path)

    IpodManager(mount).push_file_tags_to_db()
    assert iTunesDB.read(mount.itunesdb_path).tracks[0].grouping == "vibe:05"


# ------------------------------------------------------------------ tuning

def test_assign_ignores_redundant_colour_measures():
    from vibe_tagger.cluster import assign

    calm = [_vector(-40, 0.5) for _ in range(4)]
    loud = [_vector(-8, 3.0) for _ in range(4)]
    # Noisiness and timbre_1 duplicate brightness and carry no weight, so making
    # half of each group noisy must not split the groups along that line.
    for song in calm[:2] + loud[:2]:
        song[FEATURES.index("noisiness")] = 50.0
        song[FEATURES.index("timbre_1")] = -50.0
    vibes = assign(calm + loud, clusters=2)
    assert set(vibes[:4]) == {1} and set(vibes[4:]) == {2}


def test_describe_ranks_a_vibe_against_the_others():
    from vibe_tagger.cluster import describe

    groups = [[_vector(energy, 1.0), _vector(energy + 1, 1.0)] for energy in (-30, -20, -10)]
    assert "loud" in describe(groups[2], groups).split(", ")
    assert "quiet" in describe(groups[0], groups).split(", ")
    assert "medium loudness" in describe(groups[1], groups).split(", ")


def test_default_is_twenty_vibes():
    assert cli.DEFAULT_CLUSTERS == 20


def test_prune_forgets_missing_songs_only_under_scanned_folders(tmp_path):
    music, elsewhere = tmp_path / "music", tmp_path / "ipod"
    music.mkdir()
    kept = music / "kept.mp3"
    kept.write_bytes(b"x")
    cache = {
        str(kept.resolve()): {},
        str((music / "gone.mp3").resolve()): {},
        str((elsewhere / "unmounted.mp3").resolve()): {},
    }
    assert cli.prune(cache, [str(music)]) == 1
    assert set(cache) == {str(kept.resolve()), str((elsewhere / "unmounted.mp3").resolve())}


def test_cli_prunes_cache_of_deleted_songs(library):
    music, _ = library
    cli.main([str(music), "--clusters", "2"])
    (music / "calm0.mp3").unlink()
    cli.main([str(music), "--clusters", "2"])
    cache = cli._load_cache(cli.default_cache_path())
    assert not any(key.endswith("calm0.mp3") for key in cache)
    assert len(cache) == 5


# ------------------------------------------------------------------ invalid values

def test_analyze_cleans_nan_samples_and_rejects_invalid_results(monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("librosa")
    from pathlib import Path
    from vibe_tagger import features

    t = np.arange(features.SAMPLE_RATE * 5) / features.SAMPLE_RATE
    audio = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    audio[1000:2000] = np.nan
    monkeypatch.setattr(features, "load_excerpt", lambda path: audio.copy())
    assert all(np.isfinite(features.analyze(Path("song.mp3"))))

    monkeypatch.setattr(features, "_measure", lambda y, sr: [float("nan")] * len(FEATURES))
    with pytest.raises(ValueError, match="invalid values"):
        features.analyze(Path("song.mp3"))


def test_cli_reanalyses_cached_nan_entries(library):
    music, calls = library
    cli.main([str(music), "--clusters", "2"])
    cache = cli._load_cache(cli.default_cache_path())
    poisoned = next(iter(cache))
    cache[poisoned]["features"][0] = float("nan")
    cli._save_cache(cli.default_cache_path(), cache)
    before = len(calls)
    assert cli.main([str(music), "--clusters", "2"]) == 0
    assert len(calls) == before + 1


# ------------------------------------------------------------------ CPU load

def test_default_jobs_use_half_the_cores(monkeypatch):
    monkeypatch.setattr(cli.os, "cpu_count", lambda: 10)
    assert cli.default_jobs() == 5
    monkeypatch.setattr(cli.os, "cpu_count", lambda: 1)
    assert cli.default_jobs() == 1


def test_workers_are_limited_to_one_library_thread(library):
    music, _ = library
    cli.main([str(music), "--clusters", "2"])
    assert all(cli.os.environ[name] == "1" for name in cli._SINGLE_THREAD)
