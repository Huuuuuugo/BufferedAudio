import time

import pytest

from seamless_audio import DataProperties, BufferManager

def test_playing_time_accuracy():
    """Test if the `playing_time` property accurately reflects the elapsed playback time.

    This test starts playback, waits for a known duration, and then checks that the
    playing_time property is close to the expected elapsed time within a small margin of error.
    """

    buffer = BufferManager(0.5, 32000)
    buffer.play()
    time.sleep(0.5)
    assert round(buffer.playing_time, 3) in [0.500, 0.501]
    buffer.stop()