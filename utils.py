import threading

class CriticalThread(threading.Thread):
    """Child class of threading.Thread that acts mostly the same way, but also re-raises the last exception raised by any CriticalThread on the main thread.
    \nIn order for it to work, CriticalThread.check_exceptions() must be called periodically on the main thread."""
    exception_flag = threading.Event()
    exception = None

    @classmethod
    def check_exceptions(cls):
        """When CriticalThread.check_exceptions() is called on the main thread, it checks if any exception was raised on any active CriticalThread, if so, this exception is raised again on the main thread, finishing the program if not handled."""
        if cls.exception_flag.wait(0.0001):
            raise cls.exception
    
    def run(self):
        try:
            super(CriticalThread, self).run()

        except Exception as e:
            CriticalThread.exception_flag.set()
            CriticalThread.exception = e