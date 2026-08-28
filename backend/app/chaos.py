"""Seeded randomness. Same SEED -> same run, which is what makes a candidate's
harness output reproducible and their bug reports checkable."""
import random
from . import config

_rng = random.Random(config.SEED)


def hit(rate: float) -> bool:
    """True with probability `rate`, from the seeded stream."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    return _rng.random() < rate


def jitter_ms(lo: int, hi: int) -> int:
    return _rng.randint(lo, hi)


def pick(seq):
    return _rng.choice(list(seq))


def uniform(lo: float, hi: float) -> float:
    return _rng.uniform(lo, hi)
