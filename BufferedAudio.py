import soundfile as sf
import sounddevice as sd
import numpy as np
from enum import Enum
import threading
import typing
import time
import os

from utils import CriticalThread
# TODO: properly organize everything into a module


class DataProperties():
    """Creates an object with all the important information about the audio."""
    def __init__(self, data: tuple[np.ndarray[typing.Any, np.dtype[np.float64]]], info: sf._SoundFileInfo):
        self.data = data[0]
        self.samplerate = info.samplerate
        self.size = len(data[0])
        self.duration = info.duration
    
    @staticmethod
    def read_file(file_path: str):
        """Reads the file and organizes its contents into a DataProperties object."""
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
    
    def trim(self, wait=0.05):
        """Clears the already read portion of the buffer.
        \nUses a comparison between self.playing_time and self.last_written_byte to decide what to trim."""
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
    
    def append(self, data: DataProperties):
        """Appends an audio file to the end of the buffer. Also overflows back to the beggining of the buffer when the end is reached."""
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
    
    def safe_append(self, file_path: str, wait_type: typing.Literal[Modes.WAIT_SLEEP, Modes.WAIT_INTERRUPT] = Modes.WAIT_SLEEP):
        """Waits for a large enough chunk of the buffer to be trimmed before appending the data."""
        # get data and check if it fits the buffer
        data = DataProperties.read_file(file_path)
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
        print("APPENDED","| name:",file_path,"| first_byte:",first_byte,"| final_byte:",final_byte,"| duration:",data.duration)

    def insert_at_playhead(self, file_path: str, insert_mode: typing.Literal[Modes.INSERT_TRIM, Modes.INSERT_KEEP] = Modes.INSERT_TRIM):
        """Forces an audio file to be played immediatelly and sets the last used byte to after that file, essencially clearing the rest of the buffer."""
        # TODO: add an INSERT_OFFSET mode: inserts the file at the pointer and offsets what was already there to after the inserted file.
        print(f"total time before: {self.total_time_left}")
        data = DataProperties.read_file(file_path)

        if insert_mode == BufferManager.Modes.INSERT_TRIM:
            self.last_written_byte = int((self.playing_time)*self.samplerate)
            self.files_queue.clear()
            self.interrupt_safe_append = True
            self.append(data)
            self.total_time_left = data.duration
            self.trim()

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
        \nPositive values will increase the volume by n decibels.
        \nNegative values will decrease the volume by n decibels.
        \nZero will reset it to the original unchanged value."""
        if volume:
            self.volume += volume
            self.buffer[:] *= 10**(volume/20)
            self.volume_scale = 10**(self.volume/20)
        else:
            self.volume = 0
            self.buffer[:] /= self.volume_scale
            self.volume_scale = 10**(volume/20)

    def __time_tracker(self):
        """Intended for use only inside of the _play() method. 
        \nConstantly updates self.playing_time to keep up with the bytes being played at the momment.
        \nCurrently works separate from the playing thread, which could maybe cause desynchronization."""
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
                    self.buffer[:] = 0
                    clear_toggle = False
                    print("FINAL TRIM")

                # if the buffer has already been cleared, but the total_time_left was increased since then
                elif not clear_toggle and self.total_time_left > 0:
                    # sinalize that the buffer need to be cleared
                    clear_toggle = True
                    print("TOGGLED FT")

                time.sleep(1/1000)

    def play(self):
        """Plays the audio from the buffer."""
        # wait for something to be written to the buffer before starting to play
        while np.all(self.buffer == 0):
            pass
        tracker = CriticalThread(target=self.__time_tracker, args=(), daemon=True)
        
        sd.play(self.buffer, self.samplerate, loop=True)
        tracker.start()
    
    def wait_and_play(self):
        """Waits on a separate thread for something to be written to the buffer before starting to play."""
        # calls the _play() functiion and make it wait on a separate thread
        play = CriticalThread(target=self.play, args=(), daemon=True)
        play.start()
    
    def __queue_manager(self):
        """Should be called only inside of the play() method.
        \nAppends the files from the queue when apropriate."""
        while True:
            if self.files_queue:
                file_name = self.files_queue.pop(0)
                print(f"APPENDING: {file_name}")
                self.safe_append(file_name, BufferManager.Modes.WAIT_INTERRUPT)
            time.sleep(1/40)
    
    def enqueue(self, file_name: str):
        """Adds elements to the queue."""
        if not os.path.exists(file_name):
            message = f"The file '{file_name}' does not exist."
            raise FileNotFoundError(message)
        
        if not self._queue_manager.is_alive():
            self._queue_manager.start()

        self.files_queue.append(file_name)
        CriticalThread.check_exceptions()



if __name__ == "__main__":
    bf = BufferManager(8, file_sample="ignore/Track_096.ogg", volume=-5)

    bf.wait_and_play()
    with open("ignore/GTA SA Radio.m3u8", 'r') as playlist:
        for line in playlist:
            path = "C:\\VSCode\\JavaScript\\GTASARADIO\\audio\\"
            line = path + line[line.find("STREAMS")+7:].replace('.mp3', '.ogg').rstrip()
            bf.enqueue(line)

    input()