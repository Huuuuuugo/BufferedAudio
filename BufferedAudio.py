import soundfile as sf
import sounddevice as sd
import numpy as np
import threading
import typing
import time
import os


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
                message = "missing samplerate. \nThe desired samplerate must be passed to the function by one of two ways: \n1. passing an explicit value on the 'samplerate' argument; or \n2. passing the path of a file with the desired samplerate on the 'file_sample' argument"
                raise ValueError(message)
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
        self.clear_queue_flag = False
    
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
            message = f"the size of the file ({round(data.size/1000000/2*1.048576, 2)}mb) is bigger than the size of the buffer ({round(self.buffer_size/1000000/2*1.048576, 2)}mb)"
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
    
    def safe_append(self, file_path: str, wait_type: typing.Literal["sleep", "queue"] = "sleep"):
        """Waits for a large enough chunk of the buffer to be trimmed before appending the data."""
        # get data and check if it fits the buffer
        data = DataProperties.read_file(file_path)
        if data.size > self.buffer_size:
            message = f"the size of the file ({round(data.size/1000000/2*1.048576, 2)}mb) is bigger than the size of the buffer ({round(self.buffer_size/1000000/2*1.048576, 2)}mb)"
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

        # wait for those bytes to be read and then trim the buffer
        if wait_type == "sleep":
            time.sleep(time_needed)

        elif wait_type == "queue":
            time_needed += self.playing_time
            if overflow:
                curr_playing_time = self.playing_time
                while curr_playing_time <= self.playing_time < self.buffer_time:
                    if self.clear_queue_flag:
                        print("THREAD INTERRUPTED")
                        self.clear_queue_flag = False
                        return
                    time.sleep(1/40)
                time_needed -= self.buffer_time
            while self.playing_time < time_needed:
                if self.clear_queue_flag:
                    print("THREAD INTERRUPTED")
                    self.clear_queue_flag = False
                    return
                time.sleep(1/40)
            self.trim()
            
        else:
            message = f"wait_type expected to be 'sleep' or 'queue', got '{wait_type}' instead"
            raise AttributeError(message)

        # append the file on the cleared space
        self.append(data)
        print("APPENDED","| name:",file_path,"| first_byte:",first_byte,"| final_byte:",final_byte,"| duration:",data.duration)

    def insert_at_playhead(self, file_path: str):
        """Forces an audio file to be played immediatelly and sets the last used byte to after that file, essencially clearing the rest of the buffer."""
        self.files_queue.clear()
        self.clear_queue_flag = True
        self.last_written_byte = int((self.playing_time)*self.samplerate)
        data = DataProperties.read_file(file_path)
        self.append(data)
        self.trim()
          
    # TODO: force_and_keep(): function to force an audio but keep the buffer the way it was, with the same last_used_byte as before
        # last_used_byte could be the previous value (if grater tha the newer one), or the new one (which is the default on self.append)
    
    # TODO: force_and_continue(): inserts the file at the pointer and offsets what was already there to the end of the inserted file

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
        print("CALLED!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        while True:
            self.playing_time = 0
            start_timer = time.perf_counter()
            while self.playing_time <= self.buffer_time:
                curr_timer = time.perf_counter()
                self.playing_time = curr_timer - start_timer
                time.sleep(1/1000)

    def play(self):
        """Plays the audio from the buffer."""
        # wait for something to be written to the buffer before starting to play
        while np.all(self.buffer == 0):
            pass
        tracker = threading.Thread(target=self.__time_tracker, args=(), daemon=True)
        
        sd.play(self.buffer, self.samplerate, loop=True)
        tracker.start()
    
    def wait_and_play(self):
        """Waits on a separate thread for something to be written to the buffer before starting to play."""
        # calls the _play() functiion and make it wait on a separate thread
        play = threading.Thread(target=self.play, args=(), daemon=True)
        play.start()
    
    def __queue_manager(self):
        """Should be called only inside of the play() method.
        \nAppends the files from the queue when apropriate."""
        while True:
            if self.files_queue:
                file_name = self.files_queue.pop(0)
                print(f"APPENDING: {file_name}")
                self.safe_append(file_name, "queue")
            time.sleep(1/40)
    
    def start_queue_manager(self):
        """Starts the queue_manager thread.
        \nAppends the files from the queue when apropriate."""
        queue_manager = threading.Thread(target=self.__queue_manager, args=(), daemon=True)
        queue_manager.start()
    
    def enqueue(self, file_name: str):
        """Adds elements to the queue."""
        if not os.path.exists(file_name):
            message = f"the file '{file_name}' does not exist"
            raise ValueError(message)
        self.files_queue.append(file_name)            



if __name__ == "__main__":
    bf = BufferManager(4, file_sample="ignore/Track_096.ogg", volume=-5)

    bf.wait_and_play()
    bf.start_queue_manager()
    with open("ignore/GTA SA Radio.m3u8", 'r') as playlist:
        for line in playlist:
            path = "C:\\VSCode\\JavaScript\\GTASARADIO\\audio\\"
            # print(line)
            line = path + line[line.find("STREAMS")+7:].replace('.mp3', '.ogg').rstrip()
            # print(line)
            bf.enqueue(line)
            # input()
    input()
    print(bf.files_queue)
    input()
    bf.insert_at_playhead("ignore/Track_040.ogg")
    print(bf.files_queue)
    # time.sleep(10)
    input()
    bf.enqueue("ignore/Track_099.ogg")
    input()