import soundfile as sf
import sounddevice as sd
import numpy as np
import threading
import time


class DataProperties():
    """Creates an object with all the important information about the audio."""
    def __init__(self, data, info):
        self.data = data[0]
        self.samplerate = info.samplerate
        self.size = len(data[0])
        self.duration = info.duration


class BufferManager():
    def __init__(self, buffer_size: int, file_example: str):
        self.buffer = np.ndarray(shape=(buffer_size, 2), dtype="float32")
        self.buffer[:] = 0
        self.buffer_size = buffer_size
        self.samplerate = sf.info(file_example).samplerate
        self.last_used_byte = 0
        self.playing_position = 0
    

    @staticmethod
    def read_file(file_path: str):
        data = sf.read(file_path)
        info = sf.info(file_path)
        return DataProperties(data, info)
    

    def append(self, file_path: str):
        """Appends an audio file to the end of the buffer. Also overflows back to the beggining of the buffer when the end is reached."""
        data = self.read_file(file_path)
        new_last_used_byte = self.last_used_byte + data.size

        # checks if the appended data will need to overflow
        if new_last_used_byte > self.buffer_size:
            overflow = True
        else:
            overflow = False

        if overflow:
            # get ammount of bytes before overflow and determine de size of each segment (before and after oveflow)
            bytes_before_overflow = self.buffer_size - self.last_used_byte
            size_of_first_slice = bytes_before_overflow
            size_of_second_slice = data.size - bytes_before_overflow

            # write the pre-overflow and then the overflowed portion of the audio file
            self.buffer[self.last_used_byte:] = data.data[:size_of_first_slice]
            self.buffer[0:size_of_second_slice] = data.data[size_of_first_slice:size_of_second_slice]

            # update last_used_byte
            self.last_used_byte = size_of_second_slice

        else:
            # just write the audio file to the buffer and then update last_used_byte
            self.buffer[self.last_used_byte:new_last_used_byte] = data.data
            self.last_used_byte = new_last_used_byte


    def force_now(self, file_path):
        """Forces an audio file to be played imediatelly and sets the last used byte to after that file, essencially clearing the rest of the buffer."""
        # TODO: make it a force_and_clear after updating creating the main function and updating self.append
        self.last_used_byte = self.playing_position
        self.append(file_path)
        
    
    # TODO: create a function to force an audio but keep the buffer the way it was, with the same last_used_byte as before
        # last_used_byte could be the previous value (if grater tha the newer one), or the new one (which is the default on self.append)


    def clear(self, start: int = None, end: int = None):
        if start is None:
            start = 0
        
        if end is None:
            self.buffer[start:] = 0
        else:
            self.buffer[start:end] = 0


    def _position_tracker(self):
        """Intended for use only inside of the play() method. 
        \nConstantly updates self.playing_position to keep up with the bytes being played at the momment.
        \nCurrently work separate from the playing thread, which could cause desyncronization."""
        rate = self.samplerate//5
        wait = 1/5

        while True:
            for position in range(0, self.buffer_size+rate, rate):
                self.playing_position = position
                time.sleep(wait)


    def play(self, track_position=True):
        if track_position:
            tracker = threading.Thread(target=self._position_tracker, args=(), daemon=True)
        
        sd.play(self.buffer, self.samplerate, loop=True)
        tracker.start()

    
    # create the main manager loop
        # if new_last_used_byte > self.playing_position
            # wait to append
        # else
            # append
            
    



if __name__ == "__main__":
    info = BufferManager.read_file("ignore/Track_096.ogg")
    info1 = BufferManager.read_file("ignore/Track_040.ogg")
    bf = BufferManager(10000000, "ignore/Track_096.ogg")

    bf.append("ignore/Track_096.ogg")
    # print(info.size)
    # print(info.duration)
    bf.play()

    bf.append("ignore/Track_095.ogg")

    # time.sleep(5)
    # print(bf.playing_position)
    bf.force_now("ignore/cavalo.mp3")
    # # bf.buffer[bf.pos:bf.pos+info1.size] = info1.data

    bf.append("ignore/Track_099.ogg")

    # time.sleep(info.duration)
    # bf.clear(0, info.size)

    # bf.append("ignore/Track_099.ogg")

    # time.sleep(info1.duration)
    # bf.clear(info.size, info.size + info1.size)

    input("SERÁ???")