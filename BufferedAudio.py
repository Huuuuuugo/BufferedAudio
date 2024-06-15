import soundfile as sf
import sounddevice as sd
import numpy as np
import time


class DataProperties():
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
    
    @staticmethod
    def read_file(file_path: str):
        data = sf.read(file_path)
        info = sf.info(file_path)
        return DataProperties(data, info)
    
    def append(self, file_path):
        data = self.read_file(file_path)
        new_last_used_byte = self.last_used_byte + data.size

        # checks if the appended data will need to overflow
        if new_last_used_byte > self.buffer_size:
            overflow = True
        else:
            overflow = False


        if overflow:
            print("OVERFLOWED")
            # get amount before overflow
            # write that ammount to end of buffer
            # write the rest to beggining of buffer
            # another function will be responsible for handling when to write to buffer
            bytes_before_overflow = self.buffer_size - self.last_used_byte
            size_of_first_slice = bytes_before_overflow
            size_of_second_slice = data.size - bytes_before_overflow

            print(size_of_second_slice, size_of_first_slice)

            self.buffer[self.last_used_byte:] = data.data[:size_of_first_slice]
            self.buffer[0:size_of_second_slice] = data.data[size_of_first_slice:size_of_second_slice]

            self.last_used_byte = size_of_second_slice


        else:
            self.buffer[self.last_used_byte:new_last_used_byte] = data.data
            self.last_used_byte = new_last_used_byte


    def clear(self, start: int = None, end: int = None):
        if start is None:
            start = 0
        
        if end is None:
            self.buffer[start:] = 0
        else:
            self.buffer[start:end] = 0


    def play(self):
        sd.play(self.buffer, self.samplerate, loop=True)



if __name__ == "__main__":
    info = BufferManager.read_file("Track_096.ogg")
    info1 = BufferManager.read_file("Track_095.ogg")
    bf = BufferManager(info.size + info1.size, "Track_096.ogg")
    print(info.size + info1.size)

    bf.append("Track_096.ogg")
    bf.play()

    bf.append("Track_095.ogg")

    time.sleep(info.duration)
    bf.clear(0, info.size)

    bf.append("Track_099.ogg")

    time.sleep(info1.duration)
    bf.clear(info.size, info.size + info1.size)

    input("SERÁ???")