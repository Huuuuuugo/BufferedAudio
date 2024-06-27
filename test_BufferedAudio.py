import time
import pytest

from BufferedAudio import BufferManager, DataProperties
from utils import CriticalThread


# TODO: change the files used on the tests to some public samples
def insert_at_playhead_test_template(buffer_size, insert_mode):
    # template for testing the 'BufferManager.insert_at_playhead()' method
    bf = BufferManager(buffer_size, file_sample="ignore/Track_096.ogg", volume=-100)

    bf.wait_and_play()
    bf.enqueue("ignore/Track_040.ogg")
    time.sleep(1)
    bf.insert_at_playhead("ignore/Track_040.ogg", insert_mode=insert_mode)

    print(bf.total_time_left)
    assert bf.total_time_left - 11.3 < 0.05

def test_insert_at_playhead_INSERT_KEEP_total_time_left_UPDATE_OVERFLOW():
    # tests if 'BufferManager.insert_at_playhead()' 
    # with 'insert_mode=INSERT_KEEP' 
    # updates 'BufferManager.total_time_left' correctly when an OVERFLOW OCCURS

    insert_at_playhead_test_template(0.2, BufferManager.Modes.INSERT_KEEP)

def test_insert_at_playhead_INSERT_KEEP_total_time_left_UPDATE_NOT_OVERFLOW():
    # tests if 'BufferManager.insert_at_playhead()' 
    # with 'insert_mode=INSERT_KEEP' 
    # updates 'BufferManager.total_time_left' correctly when an OVERFLOW does NOT OCCUR

    insert_at_playhead_test_template(0.3, BufferManager.Modes.INSERT_KEEP)

def test_insert_at_playhead_INSERT_TRIM_total_time_left():
    # tests if 'BufferManager.insert_at_playhead()' 
    # with 'insert_mode=INSERT_TRIM' 
    # updates 'BufferManager.total_time_left' correctly

    insert_at_playhead_test_template(0.2, BufferManager.Modes.INSERT_TRIM)

def test_CriticalThread_check_exceptions():
    # tests if CriticalThread.check_exceptions() is indeed re-raising the exception from a thread on the main thread
    def test_thread():
        raise ValueError
    
    CriticalThread(target=test_thread, args=(), daemon=True).start()
    with pytest.raises(ValueError):
        CriticalThread.check_exceptions()

if __name__ == "__main__":
    test_insert_at_playhead_INSERT_KEEP_total_time_left_UPDATE_OVERFLOW()
    test_insert_at_playhead_INSERT_KEEP_total_time_left_UPDATE_NOT_OVERFLOW()
    test_insert_at_playhead_INSERT_TRIM_total_time_left()