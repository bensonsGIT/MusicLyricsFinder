"""Group songs that sound alike into numbered vibes."""

import numpy as np

from .features import FEATURES

# Tuned on a 8,700-song library: noisiness and the first timbre coefficient track
# brightness almost exactly (|r| 0.84-0.94), so only brightness counts; timbre
# carries the most information about sound; tempo and major/minor estimates are
# noisy, so they count half. They stay in FEATURES so cached analyses remain valid.
_WEIGHT_OVERRIDES = {"noisiness": 0.0, "timbre_1": 0.0, "tempo": 0.5, "major": 0.5}
_WEIGHTS = np.array([_WEIGHT_OVERRIDES.get(name, 1.0) for name in FEATURES])


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


def describe(vectors: list[list[float]], groups: list[list[list[float]]]) -> str:
    """A short summary of one vibe relative to the others.

    Group averages crowd towards the library average, so each measure is ranked
    against the other vibes' averages rather than against individual songs.
    """
    group = np.asarray(vectors, dtype=float).mean(axis=0)
    means = np.array([np.asarray(g, dtype=float).mean(axis=0) for g in groups])

    def level(name, low, mid, high):
        index = FEATURES.index(name)
        lower, upper = np.percentile(means[:, index], [33.3, 66.7])
        value = group[index]
        return low if value < lower else high if value > upper else mid

    return ", ".join([
        level("tempo", "slower", "mid-tempo", "faster"),
        level("energy", "quiet", "medium loudness", "loud"),
        level("percussiveness", "smooth", "steady beat", "driving beat"),
        level("dynamics", "even", "some contrast", "punchy"),
        level("brightness", "dark/warm", "balanced tone", "bright"),
        level("major", "minor-leaning", "neutral key", "major-leaning"),
        level("drums", "mostly tonal", "balanced mix", "drum-heavy"),
        level("bass", "light bass", "moderate bass", "bass-heavy"),
        level("steadiness", "loose rhythm", "regular rhythm", "locked groove"),
        level("contrast", "dense mix", "medium density", "sparse mix"),
    ])
