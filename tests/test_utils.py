import pytest

from seamless_audio.utils import CriticalThread


def test_CriticalThread_wait_exception():
    # tests if CriticalThread.check_exceptions() is indeed re-raising the exception from a thread on the main thread
    def test_thread():
        raise ValueError
    
    CriticalThread(target=test_thread, args=(), daemon=True).start()
    with pytest.raises(ValueError):
        CriticalThread.wait_exception()