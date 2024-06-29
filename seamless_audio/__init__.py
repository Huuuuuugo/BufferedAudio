from seamless_audio.buffer import DataProperties, BufferManager

Modes = BufferManager.Modes

def read_file(file_path: str):
    DataProperties.read_file(file_path)