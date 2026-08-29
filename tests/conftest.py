import pytest

from pc_sound_recorder import stt


@pytest.fixture(autouse=True)
def _kein_warmes_modell():
    """Das warm gehaltene Diktatmodell zwischen den Tests wegräumen.

    `stt._cache` ist Modulzustand. Ohne dieses Aufräumen fände ein Test das
    Attrappenmodell des vorigen vor — mit demselben Schlüssel (Modell, Gerät,
    Quantisierung) fragt `_cached_model()` `load_model` gar nicht erst, und ein
    Test, der genau das zählt, prüfte dann nichts.
    """
    yield
    stt.release_model()
