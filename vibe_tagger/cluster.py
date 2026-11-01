"""Group songs that sound alike into numbered vibes."""

import numpy as np

from .features import FEATURES

# Timbre spans many coefficients; down-weight each so it does not swamp pace,
# intensity and mood, which matter most to how a song feels.
_WEIGHTS = np.array([0.5 if name.startswith("timbre_") else 1.0 for name in FEATURES])


def assign(vectors: list[list[float]], clusters: int, seed: int = 0) -> list[int]:
    """Return a 1-based vibe number per vector.

    Vibes are numbered from calmest to most intense (by mean energy plus
    percussiveness), so nearby numbers feel related.
    """
    from sklearn.cluster import KMeans

    data = np.asarray(vectors, dtype=float)
    spread = data.std(axis=0)
    spread[spread == 0] = 1.0
    scaled = (data - data.mean(axis=0)) / spread * _WEIGHTS
    k = max(1, min(clusters, len(data)))
    labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(scaled)

    energy, percussive = FEATURES.index("energy"), FEATURES.index("percussiveness")
    intensity = scaled[:, energy] + scaled[:, percussive]
    order = sorted(range(k), key=lambda c: intensity[labels == c].mean())
    rank = {cluster: position + 1 for position, cluster in enumerate(order)}
    return [rank[label] for label in labels]


def describe(vectors: list[list[float]]) -> str:
    """A short human summary of a group of feature vectors."""
    mean = np.asarray(vectors, dtype=float).mean(axis=0)
    tempo = mean[FEATURES.index("tempo")]
    energy = mean[FEATURES.index("energy")]
    brightness = mean[FEATURES.index("brightness")]
    major = mean[FEATURES.index("major")]
    return (f"~{tempo:.0f} BPM, {energy:.0f} dB, "
            f"{'bright' if brightness > 2.0 else 'warm'}, "
            f"{'major' if major > 0 else 'minor'}-leaning")
