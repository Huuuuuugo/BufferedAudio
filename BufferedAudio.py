import soundfile as sf
import sounddevice as sd
import numpy as np
import threading
import typing
import time


class DataProperties():
    """Creates an object with all the important information about the audio."""
    def __init__(self, data: tuple[np.ndarray[typing.Any, np.dtype[np.float64]]], info: sf._SoundFileInfo):
        self.data = data[0]
        self.samplerate = info.samplerate
        self.size = len(data[0])
        self.duration = info.duration


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
        self._volume_scale = 10**(volume/20)

    @staticmethod
    def read_file(file_path: str):
        """Reads the file and organizes its contents into a DataProperties object."""
        data = sf.read(file_path)
        info = sf.info(file_path)
        return DataProperties(data, info)
    
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
                self.buffer[self.last_written_byte:] = data.data[:size_of_first_slice] * self._volume_scale
                self.buffer[0:size_of_second_slice] = data.data[size_of_first_slice:] * self._volume_scale

                # update last_used_byte
                self.last_written_byte = size_of_second_slice

            else:
                # just write the audio file to the buffer and then update last_used_byte
                self.buffer[self.last_written_byte:new_last_used_byte] = data.data * self._volume_scale
                self.last_written_byte = new_last_used_byte

    def force_now(self, file_path: str):
        """Forces an audio file to be played immediatelly and sets the last used byte to after that file, essencially clearing the rest of the buffer."""
        # TODO: make it work properlly and turn it into force_and_trim()
        self.last_written_byte = int((self.playing_time)*self.samplerate)
        data = self.read_file(file_path)
        self.append(data)
          
    # TODO: force_and_keep(): function to force an audio but keep the buffer the way it was, with the same last_used_byte as before
        # last_used_byte could be the previous value (if grater tha the newer one), or the new one (which is the default on self.append)

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

    def _time_tracker(self):
        """Intended for use only inside of the _play() method. 
        \nConstantly updates self.playing_time to keep up with the bytes being played at the momment.
        \nCurrently works separate from the playing thread, which could maybe cause desynchronization."""
        while True:
            self.playing_time = 0
            start_timer = time.perf_counter()
            while self.playing_time <= self.buffer_time:
                curr_timer = time.perf_counter()
                self.playing_time = curr_timer - start_timer
                time.sleep(1/1000)

    def play(self):
        """Plays the audio buffer. 
        \nAlso waits on a separate thread for something to be written to the buffer before starting to play."""
        # calls the _play() functiion and make it wait on a separate thread
        play = threading.Thread(target=self._play, args=(), daemon=True)
        play.start()

    def _play(self):
        """Intended for use only inside of the play() method."""
        # wait for something to be written to the buffer before starting to play
        while np.all(self.buffer == 0):
            pass
        tracker = threading.Thread(target=self._time_tracker, args=(), daemon=True)
        
        sd.play(self.buffer, self.samplerate, loop=True)
        tracker.start()

    def safe_append(self, file_path: str):
        """Waits for a large enough chunk of the buffer to be trimmed before appending the data."""
        # TODO: create thread to manage appending without interrupting the flow of the program
        # get data and check if it fits the buffer
        data = self.read_file(file_path)
        if data.size > self.buffer_size:
            message = f"the size of the file ({round(data.size/1000000/2*1.048576, 2)}mb) is bigger than the size of the buffer ({round(self.buffer_size/1000000/2*1.048576, 2)}mb)"
            raise MemoryError(message)

        # get the first and final byte that the audio file will need
        overflow = False
        final_byte = self.last_written_byte + data.size
        first_byte = self.last_written_byte

        # check if the file will need to overflow the buffer
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
        time.sleep(time_needed)
        self.trim()
        print("missing bytes #2:",missing_bytes)

        # append the file on the cleared space
        self.append(data)
        print("APPENDED","| name:",file_path,"| first_byte:",first_byte,"| final_byte:",final_byte,"| duration:",data.duration)
    
    def change_volume(self, volume: int):
        """Changes the loudness of the audio by n decibels.
        \nPositive values will increase the volume by n decibels.
        \nNegative values will decrease the volume by n decibels.
        \nZero will reset it to the original unchanged value."""
        if volume:
            self.volume += volume
            self.buffer[:] *= 10**(volume/20)
            self._volume_scale = 10**(self.volume/20)
        else:
            self.volume = 0
            self.buffer[:] /= self._volume_scale
            self._volume_scale = 10**(volume/20)
    



if __name__ == "__main__":
    bf = BufferManager(4, file_sample="ignore/Track_096.ogg", volume=0)

    bf.play()
    with open("ignore/GTA SA Radio.m3u8", 'r') as playlist:
        for line in playlist:
            path = "C:\\VSCode\\JavaScript\\GTASARADIO\\audio\\"
            print(line)
            line = path + line[line.find("STREAMS")+7:].replace('.mp3', '.ogg').rstrip()
            # print(line)
            bf.safe_append(line)
            # input()