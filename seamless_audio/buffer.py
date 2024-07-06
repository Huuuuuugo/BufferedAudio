from enum import Enum
import threading
import typing
import time
import os

import soundfile as sf
import sounddevice as sd
import numpy as np

from seamless_audio.utils import CriticalThread
# TODO: test with mono audio
# TODO: maybe create a function to normalize the samplerate of the files
# TODO: stop method method from sd
# TODO: rewrite play() to allow playing multiple buffers simultaneouslly
# TODO: change BufferManager.files_queue to a real queue or add option to manipulate it as a list (indexing, slicing etc)
# TODO: improve the docstrings
# TODO: cleanup the debug prints
# TODO: make an insert_anywhere function


class DataProperties():
    """Groups the audio data and all the relevant information from a file inside a single object.
    
    This class should only be instantiated using the `read_file` static method or its alias inside `__init__.py`.

    Attributes
    ----------
    data: np.ndarray
        The audio data from the file. That's what goes inside the buffer.

    samplerate: int
        The samplerate of the audio in bits per second.

    size: int
        The total size of the file in bytes.

    duration: float
        The duration of the file in seconds.

    Parameters
    ----------
    data: np.ndarray
        The audio data from `sf.read`.

    info: sf._SoundFileInfo
        The SoundFileInfo object from `sf.info`
    """

    def __init__(self, data: tuple[np.ndarray[typing.Any, np.dtype[np.float64]]], info: sf._SoundFileInfo):
        """This class should only be instantiated using the `read_file` static method or its alias inside `__init__.py`."""

        self.data = data[0]
        self.samplerate = info.samplerate
        self.size = len(data[0])
        self.duration = info.duration
    
    @staticmethod
    def read_file(file_path: str):
        """Reads a file and organizes its contents into a DataProperties object.
        
        Parameters
        ----------
        file_path: str
            String containing the path to the file that'll be read.

        Returns
        -------
        DataProperties
            Object containg the audio data and all the relevant information about the file.
        """

        data = sf.read(file_path)
        info = sf.info(file_path)
        return DataProperties(data, info)


class BufferManager():
    def __init__(self, buffer_size: float, samplerate: int=None, file_sample: str=None, volume: int = 0):
        # covert buffer_size from mb to bytes, create the array and fill it with zeros
        buffer_size = int(buffer_size*1000000*2/1.048576)
        self.buffer = np.ndarray(shape=(buffer_size, 2), dtype="float32")
        self.buffer[:] = 0

        if samplerate is None:
            if file_sample is not None:
                self.samplerate = sf.info(file_sample).samplerate
            else:
                message = "Missing samplerate. \nThe desired samplerate must be passed to the function by one of two ways: \n1. passing an explicit value on the 'samplerate' argument; or \n2. passing the path of a file with the desired samplerate on the 'file_sample' argument"
                raise AttributeError(message)
        else:
            self.samplerate = samplerate

        self.buffer_size = buffer_size
        self.buffer_time = self.buffer_size/self.samplerate
        self.last_written_byte = 0
        self.playing_time = 0
        self.buffer_lock = threading.Lock()

        self.volume = volume
        self.volume_scale = 10**(volume/20)

        self.files_queue = []
        self.interrupt_safe_append = False
        self.total_time_left = 0

        self._queue_manager = CriticalThread(target=self.__queue_manager, args=(), daemon=True)

    class Modes(Enum):
        WAIT_SLEEP = "sleep"
        WAIT_INTERRUPT = "interrupt"
        INSERT_TRIM = "trim"
        INSERT_KEEP = "keep"
    
    def trim(self, wait: float = 0.05):
        """Clears the portion of the buffer that was already played, freeing up space for new files.

        This is automatically called by `safe_append` when appropriate, so you're probably fine just using that.

        Parameters
        ----------
        wait: float
            Time to wait before trimming the buffer. Avoids clearing the portion that is currently 
            being played.
        
        Side Effects
        ------------
            - Updates the np.ndarray inside the `buffer` atrribute to remove everything that has already been played.
        """

        with self.buffer_lock:
            last_read_byte = int((self.playing_time)*self.samplerate)
            time.sleep(wait)
            if last_read_byte < 0:
                return

            if self.last_written_byte > last_read_byte:
                self.buffer[self.last_written_byte:] = 0
                self.buffer[:last_read_byte] = 0

            elif self.last_written_byte < last_read_byte:
                self.buffer[self.last_written_byte:last_read_byte] = 0
    
    def clear(self):
        """Removes everything from the buffer by filling it with zeroes."""

        self.buffer[:] = 0
    
    def append(self, data_source: str | DataProperties):
        """Directly appends an audio data to the end of the buffer and updates the `total_time_left` attribute. 
        
        Also automatically circles back to the beggining of the buffer when the end is reached.

        This method does not check if what is being overwritten is empty or has already been played or not, it just appends anyway, if 
        you don't want to deal with the right times to append a new file, just use `safe_append` or `enque`.

        If you're using this method directly, instead of `safe_append` or `enque`, the `total_time_left` attribute might not be reliable, as 
        it will not exclude the portions of the buffer that were overwritten before being played.
        
        Parameters
        ----------
        data_source: DataProperties or str
            DataProperties: the DataProperties object containing all the relevant information about the file that will be appended. Normally acquired using
            the `DataProperties.read_file` method.

            str: the path to the file that will be appended.

        Raises
        ------
        FileNotFoundError
            If data_source is a string, but not a real path.

        MemoryError
            If the size of the data being appended excedes the maximum capacity of the buffer.
        
        Side Effects
        ------------
            - Writes the given data into the `buffer`, right after the previously last written portion.
            - Updates `total_time_left` to include the duration of the new data.
        """

        # check the type of data_source
        if isinstance(data_source, DataProperties):
            data = data_source

        elif isinstance(data_source, str):
            if not os.path.exists(data_source):
                message = f"The file '{data_source}' does not exist."
                raise FileNotFoundError(message)
            data = DataProperties.read_file(data_source)

        # checks if the data fits on the buffer
        if data.size > self.buffer_size:
            message = f"The size of the file ({round(data.size/1000000/2*1.048576, 2)}mb) is bigger than the size of the buffer ({round(self.buffer_size/1000000/2*1.048576, 2)}mb)"
            raise MemoryError(message)
        
        with self.buffer_lock:
            new_last_used_byte = self.last_written_byte + data.size
            # checks if the appended data will need to overflow
            if new_last_used_byte > self.buffer_size:
                overflow = True
            else:
                overflow = False

            if overflow:
                # get ammount of bytes before overflow and determine de size of each segment (before and after oveflow)
                bytes_before_overflow = self.buffer_size - self.last_written_byte
                size_of_first_slice = bytes_before_overflow
                size_of_second_slice = data.size - bytes_before_overflow

                # write the pre-overflow and then the overflowed portion of the audio file
                self.buffer[self.last_written_byte:] = data.data[:size_of_first_slice] * self.volume_scale
                self.buffer[0:size_of_second_slice] = data.data[size_of_first_slice:] * self.volume_scale

                # update last_used_byte
                self.last_written_byte = size_of_second_slice

            else:
                # just write the audio file to the buffer and then update last_used_byte
                self.buffer[self.last_written_byte:new_last_used_byte] = data.data * self.volume_scale
                self.last_written_byte = new_last_used_byte
            
            self.total_time_left += data.duration
    
    def safe_append(self, data_source: str | DataProperties, wait_type: typing.Literal[Modes.WAIT_SLEEP, Modes.WAIT_INTERRUPT] = Modes.WAIT_SLEEP):
        """Waits for the minimum time needed to ensure the buffer is trimmed just enough to append the new data.
        
        This wait ensures that everything that is overwritten was already played. The wait happens on the main thread, if
        you don't want to interrupt its flow use `enqueue` instead.

        If you are already using `enqueue`, avoid using `safe_append` concurrently. This can lead to both methods
        waiting simultaneously in different threads, potentially disrupting the order in which data is appended.

        Parameters
        ----------
        data_source: DataProperties or str
            DataProperties: the DataProperties object containing all the relevant information about the file that will be appended. Normally acquired using
            the `DataProperties.read_file` method.

            str: the path to the file that will be appended.

        wait_type: Modes
            WAIT_SLEEP
                Sleeps untill the wait time finishes.

            WAIT_INTERRUPT 
                Interrupts the wait and cancels the appending if the `interrupt_safe_append` attribute is set to True (used inside `__queue_manager`).

        Raises
        ------
        FileNotFoundError
            If data_source is a string, but not a real path.

        MemoryError
            If the size of the data being appended excedes the maximum capacity of the buffer.

        AttributeError
            If wait_type is not a recognized value (WAIT_SLEEP or WAIT_INTERRUPT) 
        
        Side Effects
        ------------
            - Appends the data into the buffer after enough time has passed.
        """
        
        # check the type of data_source
        if isinstance(data_source, DataProperties):
            data = data_source

        elif isinstance(data_source, str):
            if not os.path.exists(data_source):
                message = f"The file '{data_source}' does not exist."
                raise FileNotFoundError(message)
            data = DataProperties.read_file(data_source)

        # check if the data fits the buffer
        if data.size > self.buffer_size:
            message = f"The size of the file ({round(data.size/1000000/2*1.048576, 2)}mb) is bigger than the size of the buffer ({round(self.buffer_size/1000000/2*1.048576, 2)}mb)"
            raise MemoryError(message)

        # get the first and final byte that the audio file will need
        final_byte = self.last_written_byte + data.size
        first_byte = self.last_written_byte

        # check if the file will need to overflow the buffer
        overflow = False
        if final_byte > self.buffer_size:
            print("OVERFLOWED")
            final_byte -= self.buffer_size
            first_byte = 0
            overflow = True
        print("WAITING...")

        # get the ammount of bytes that need to be cleared for the file to fit on the buffer
        missing_bytes = np.count_nonzero(self.buffer[first_byte:final_byte])//2
        if overflow:
            missing_bytes += np.count_nonzero(self.buffer[self.last_written_byte:])//2
        
        # get time needed for the necessary bytes to be freed
        time_needed = missing_bytes/self.samplerate
        print("missing bytes #1:",missing_bytes,"| data.size:",data.size,"| time needed:",time_needed)

        # wait for those bytes to be read
        if wait_type == BufferManager.Modes.WAIT_SLEEP:
            time.sleep(time_needed)

        elif wait_type == BufferManager.Modes.WAIT_INTERRUPT:
            time_needed += self.playing_time
            
            # if the time needed excedes the limit of the buffer
            if time_needed > self.buffer_time:
                curr_playing_time = self.playing_time
                # wait untill the playing time gets to the end of the buffer
                while curr_playing_time <= self.playing_time:
                    # check if the function should be interrupted
                    if self.interrupt_safe_append:
                        print("THREAD INTERRUPTED")
                        self.interrupt_safe_append = False
                        return
                    time.sleep(1/40)
                # subtract the time waited from the time needed
                time_needed -= self.buffer_time

            # wait for the time needed
            while self.playing_time < time_needed:
                # check if the function should be interrupted
                if self.interrupt_safe_append:
                    print("THREAD INTERRUPTED")
                    self.interrupt_safe_append = False
                    return
                time.sleep(1/40)

        else:
            message = f"Invalid value for wait_mode argument. It must be one of the WAIT constants from BufferManager.Modes."
            raise AttributeError(message)
        
        # trim the buffer
        self.trim()

        # append the file on the cleared space
        self.append(data)
        print("APPENDED","| name:", data_source,"| first_byte:",first_byte,"| final_byte:",final_byte,"| duration:",data.duration)

    def insert_at_playhead(self, data_source: str | DataProperties, insert_mode: typing.Literal[Modes.INSERT_TRIM, Modes.INSERT_KEEP] = Modes.INSERT_TRIM):
        """Immediately plays the audio by inserting it at the position being played at the moment.

        This means it will also overwrite what was already there, but there are two modes that can help you deal with it, see the `insert_mode` parameter bellow.

        This function is specially useful when first inserting something in the buffer after it has been clear, as it can reset the `last_written_byte` pointer.

        Parameters
        ----------
        data_source: DataProperties or str
            DataProperties: the DataProperties object containing all the relevant information about the file that will be appended. Normally acquired using
            the `DataProperties.read_file` method.

            str: the path to the file that will be appended.

        insert_mode: Modes
            INSERT_TRIM
                Inserts the data and clears everything besides what was just added. That means everything that was there before will be 
            lost and everything appended after it is called will come right after what was just added.

            INSERT_KEEP
                Inserts the data but keep everything that wasn't overwritten when inserting. That means only what was overwritten will be 
            lost and everything appended after it is called will come after what was previously the end.


        Raises
        ------
        FileNotFoundError
            If `data_source` is a string, but not a real path.

        MemoryError
            If the size of the data being appended excedes the maximum capacity of the buffer.

        AttributeError
            If `insert_mode` is not a recognized value (NSERT_TRIM or INSERT_KEEP) 
        
        Side Effects
        ------------
            - Overwrites a portion of the buffer starting from the playing position.
            - Depending on `insert_mode`, it can clear just the portion overwritten or the whole buffer and queue.
            - If `insert_mode` is set to INSERT_TRIM, it resets the `last_used_byte` pointer to after the inserted file.
        """
        # TODO: add an INSERT_OFFSET mode: inserts the file at the pointer and offsets what was already there to after the inserted file.

        CriticalThread.wait_exception(0)

        # check the type of data_source
        if isinstance(data_source, DataProperties):
            data = data_source

        elif isinstance(data_source, str):
            if not os.path.exists(data_source):
                message = f"The file '{data_source}' does not exist."
                raise FileNotFoundError(message)
            data = DataProperties.read_file(data_source)

        print(f"total time before: {self.total_time_left}")

        # inserts the data and clears everything besides what was just added
        if insert_mode == BufferManager.Modes.INSERT_TRIM:
            self.last_written_byte = int((self.playing_time)*self.samplerate)
            self.files_queue.clear()
            self.interrupt_safe_append = True
            self.append(data)
            self.total_time_left = data.duration
            self.trim()

        # inserts the data but keep everything that wasn't overwritten when inserting
        elif insert_mode == BufferManager.Modes.INSERT_KEEP:
            prev_last_written_byte = self.last_written_byte
            self.last_written_byte = int((self.playing_time)*self.samplerate)
            self.append(data)

            # if the new last_written_byte pointer is before the previous one and it didn't overflow
            if prev_last_written_byte > self.last_written_byte and self.last_written_byte > self.playing_time*self.samplerate:
                print("KEPT")
                # keep the previous value
                self.last_written_byte = prev_last_written_byte
                # remove the time added by append()
                self.total_time_left -= data.duration
            
            else:
                # get the correct ammount of time that should be added to total_time_left
                # if the data overflows
                if self.last_written_byte < self.playing_time*self.samplerate:
                    print("UPDATED #1 (OVERFLOWED)")
                    # get the ammount of bytes added by getting the difference between the previous and current value of the last_written_byte pointer
                    # and adding the total ammount of bytes from the buffer, to account for the overflow.
                    # then, divide the total of bytes added by the samplerate to get the equivalent in seconds.
                    time_added = (self.last_written_byte - prev_last_written_byte + self.buffer_size)/self.samplerate
                else:
                    print("UPDATED #2 (NOT OVERFLOWED)")
                    # get the ammount of bytes added by getting the difference between the previous and current value of the last_written_byte pointer.
                    # then, divide the total of bytes added by the samplerate to get the equivalent in seconds.
                    time_added = (self.last_written_byte - prev_last_written_byte)/self.samplerate

                # remove the time added by append() and add the correct ammount of time
                self.total_time_left -= data.duration
                self.total_time_left += time_added
                
        else:
            message = f"Invalid value for insert_mode argument. It must be one of the INSERT constants from BufferManager.Modes."
            raise AttributeError(message)
        
        print(f"total time after: {self.total_time_left}")
          
    def change_volume(self, volume: int):
        """Changes the loudness of the audio by n decibels.
        
        - Positive values will increase the volume by n decibels.
        - Negative values will decrease the volume by n decibels.
        - Zero will reset it to the original unchanged value.
        """

        CriticalThread.wait_exception(0)

        if volume:
            self.volume += volume
            self.buffer[:] *= 10**(volume/20)
            self.volume_scale = 10**(self.volume/20)
        else:
            self.volume = 0
            self.buffer[:] /= self.volume_scale
            self.volume_scale = 10**(volume/20)

    def __time_tracker(self):
        """Intended for use only inside of the play() method. 
        
        Constantly updates self.playing_time to keep up with the bytes being played at the momment.
        
        Currently works separate from the playing thread, which could maybe cause desynchronization.
        """

        clear_toggle = True
        while True:
            self.playing_time = 0
            start_timer = curr_timer = time.perf_counter()
            while self.playing_time <= self.buffer_time:
                prev_curr_time = curr_timer
                curr_timer = time.perf_counter()
                self.playing_time = curr_timer - start_timer

                # if the total_time_left isn't zero, subtract the time elapsed between tha previous and the current cycle
                if self.total_time_left > 0:
                    self.total_time_left -= curr_timer - prev_curr_time

                # if total_time_left reaches zero and it hasn't cleared the buffer yet
                if clear_toggle and self.total_time_left <= 0:
                    # set timer to zero, clear the buffer and sinalize that the buffer has already been cleared
                    self.total_time_left = 0
                    self.clear()
                    clear_toggle = False
                    print("FINAL TRIM")

                # if the buffer has already been cleared, but the total_time_left was increased since then
                elif not clear_toggle and self.total_time_left > 0:
                    # sinalize that the buffer need to be cleared
                    clear_toggle = True
                    print("TOGGLED FT")

                time.sleep(1/1000)

    def play(self):
        """Plays the audio from the buffer immediately, even if nothing has been written yet.
        
        You probably should use `wait_and_play` instead.
        """

        tracker = CriticalThread(target=self.__time_tracker, args=(), daemon=True)
        sd.play(self.buffer, self.samplerate, loop=True)
        tracker.start()
    
    def wait_and_play(self):
        """Waits on a separate thread for something to be written to the buffer before starting to play."""

        def _wait_and_play():
            # wait for something to be written to the buffer before starting to play
            while np.all(self.buffer == 0):
                pass
            self.play()

        # calls the play() function and make it wait on a separate thread
        wait = CriticalThread(target=_wait_and_play, args=(), daemon=True)
        wait.start()
    
    def wait_done(self):
        """Waits untill everything on the buffer is fully played, that includes the audios that are still on the queue."""

        time.sleep(0.5)
        while self.total_time_left:
            print(self.total_time_left)
            CriticalThread.wait_exception(self.total_time_left)

    def __queue_manager(self):
        """Should only be started as a thread inside of the `enqueue` method.
        
        Appends the files from the queue when apropriate.
        """
        # TODO: make it call insert_at_playhead() with ISERT_TRIM when first enqueueing after total_time_left reaches zero

        while True:
            if self.files_queue:
                file_name = self.files_queue.pop(0)
                print(f"APPENDING: {file_name}")
                self.safe_append(file_name, BufferManager.Modes.WAIT_INTERRUPT)
            time.sleep(1/40)
    
    def enqueue(self, data_source: str | DataProperties):
        """Adds elements to the queue."""
        # TODO: warn on the docstring about high memory consumption whe enqueueing a DataProperties

        CriticalThread.wait_exception(0)

        if not isinstance(data_source, DataProperties):
            if not os.path.exists(data_source):
                message = f"The file '{data_source}' does not exist."
                raise FileNotFoundError(message)
        
        if not self._queue_manager.is_alive():
            self._queue_manager.start()

        self.files_queue.append(data_source)


if __name__ == "__main__":
    bf = BufferManager(8, file_sample="ignore/Track_096.ogg", volume=-5)

    bf.wait_and_play()
    i = 0
    with open("ignore/GTA SA Radio.m3u8", 'r') as playlist:
        for line in playlist:
            path = "C:\\VSCode\\JavaScript\\GTASARADIO\\audio\\"
            line = path + line[line.find("STREAMS")+7:].replace('.mp3', '.ogg').rstrip()
            # line = DataProperties.read_file(line)
            bf.enqueue(line)
            i += 1
            if i == 4:
                break
    
    print(bf.total_time_left)
    bf.wait_done()
    print("FINISHED 👍")