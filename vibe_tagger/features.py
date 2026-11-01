"""Measure how a song sounds from a mid-song excerpt.

The features are chosen to separate "vibes": pace (tempo, percussiveness),
intensity (energy, dynamics), colour (brightness, noisiness), mood (major vs
minor) and texture (timbre coefficients).
"""

import shutil
import subprocess
from pathlib import Path

import numpy as np

TIMBRE_COEFFICIENTS = 12
FEATURES = (
    "tempo", "energy", "dynamics", "brightness", "noisiness", "percussiveness",
    "major", *(f"timbre_{i}" for i in range(1, TIMBRE_COEFFICIENTS + 1)),
)
SAMPLE_RATE = 22050
EXCERPT_SECONDS = 45.0

# Krumhansl-Kessler key profiles, rotated across all 12 tonics.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _mode(chroma: np.ndarray) -> float:
    """Best major-key fit minus best minor-key fit: positive sounds major."""
    profile = chroma.mean(axis=1)
    if not profile.any():
        return 0.0
    fit = lambda key: max(np.corrcoef(profile, np.roll(key, t))[0, 1] for t in range(12))
    return float(fit(_MAJOR) - fit(_MINOR))


def load_excerpt(path: Path) -> np.ndarray:
    """Mono samples at SAMPLE_RATE from 30% into the song, skipping intros.

    ffmpeg decodes AAC/M4A, which librosa's libsndfile backend cannot; without
    ffmpeg only formats libsndfile reads (such as MP3) work.
    """
    import mutagen

    info = getattr(mutagen.File(path), "info", None)
    duration = getattr(info, "length", 0) or 0
    offset = max(0.0, min(duration * 0.3, duration - EXCERPT_SECONDS))
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        pcm = subprocess.run(
            [ffmpeg, "-v", "error", "-ss", f"{offset:.2f}", "-t", str(EXCERPT_SECONDS),
             "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
            capture_output=True, check=True,
        ).stdout
        return np.frombuffer(pcm, dtype=np.float32)
    import librosa
    try:
        samples, _ = librosa.load(str(path), sr=SAMPLE_RATE, mono=True, offset=offset,
                                  duration=EXCERPT_SECONDS)
    except Exception as error:
        raise RuntimeError(f"{error} (install ffmpeg to read this format)") from error
    return samples


def analyze(path: Path) -> list[float]:
    """Return one value per entry in FEATURES. Raises on unreadable or tiny files."""
    import librosa

    y, sr = load_excerpt(path), SAMPLE_RATE
    if y.size < sr:
        raise ValueError("audio shorter than one second")

    rms_db = librosa.amplitude_to_db(librosa.feature.rms(y=y)[0], ref=1.0)
    onsets = librosa.onset.onset_strength(y=y, sr=sr)
    tempo = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=onsets, sr=sr))[0])
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(y=y)[0]
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    # Coefficient 0 tracks loudness, which energy already covers.
    timbre = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=TIMBRE_COEFFICIENTS + 1)[1:]

    return [
        tempo,
        float(rms_db.mean()),
        float(rms_db.std()),
        float(centroid.mean() / 1000.0),
        float(np.log10(flatness.mean() + 1e-10)),
        float(onsets.mean()),
        _mode(chroma),
        *map(float, timbre.mean(axis=1)),
    ]
