import time

import pytest

from seamless_audio import DataProperties, BufferManager

def test_playing_time_accuracy():
    """Test if the `playing_time` property accurately reflects the elapsed playback time.

    This test starts playback, waits for a known duration, and then checks that the
    playing_time property is close to the expected elapsed time within a small margin of error.
    This check is repeated for a total of 3 cycles and 5 times per cycle.

    An error here could indicate a problem with: 
        - `BufferManager.play()`; 
        - `BufferManager.stop()`;
        - too much consecutive accesses to time.perf_counter();
        - the playing_time property itself;
    """

    buffer = BufferManager(0.5, 32000)
    for _ in range(3):
        time.sleep(0.2)  # Small delay before playing to see if it will affect the time after
        buffer.play()

        for i in range(1, 6):
            time.sleep(0.1)  # Simulate playback for 0.1 seconds
            actual_time = round(buffer.playing_time, 2)
            expected_time = round(0.1*i, 2)
            assert actual_time == expected_time, f"Inaccurate result: {expected_time = } | {actual_time = }"

        buffer.stop()