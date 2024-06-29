import threading
import time
import sys


class CriticalThread(threading.Thread):
    """Child class of threading.Thread that acts mostly the same way, but also re-raises the last exception raised by any CriticalThread on the main thread.
    \nIn order for it to work, CriticalThread.check_exceptions() must be called periodically on the main thread."""
    exception_info = {}
    
    @classmethod
    def wait_exception(cls, timeout: float=None):
        """When CriticalThread.check_exceptions() is called on the main thread, it waits for any exception to be raised on any active CriticalThread, if so, this exception is raised again on the main thread, finishing the program if not handled."""
        if timeout is not None:
            curr_time = 0
            end_time = time.perf_counter() + timeout
        
            while curr_time <= end_time:
                curr_time = time.perf_counter()
                if cls.exception_info:
                    original_exception = cls.exception_info["exception"]
                    if original_exception.args:
                        new_message = f"Exception raised on {cls.exception_info["thread_name"]}: {original_exception.args[0]}"
                    else:
                        new_message = f"Exception raised on {cls.exception_info["thread_name"]}."
                    raise type(original_exception)(new_message).with_traceback(original_exception.__traceback__)
                time.sleep(1/1000)
                
        else:
            while True:
                if cls.exception_info:
                    original_exception = cls.exception_info["exception"]
                    if original_exception.args:
                        new_message = f"Exception raised on {cls.exception_info["thread_name"]}: {original_exception.args[0]}"
                    else:
                        new_message = f"Exception raised on {cls.exception_info["thread_name"]}."
                    raise type(original_exception)(new_message).with_traceback(original_exception.__traceback__)
                time.sleep(1/40)
    
    def run(self):
        try:
            super(CriticalThread, self).run()

        except Exception as e:
            self.__class__.exception_info.update({"thread_name": self.name, "exception": e})
            print(f"Warning: '{type(e).__name__}' exception raised on {self.name}.", file=sys.stderr)