"""Python 2/3 bounded JPEG verification using existing Pillow; no device I/O."""
import io
import sys
from PIL import Image, ImageFile

def main():
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise ValueError('strict decoder required')
    if sys.argv[1:] == ['--check']:
        return
    if sys.argv[1:]:
        raise ValueError('arguments')
    stream = getattr(sys.stdin, 'buffer', sys.stdin)
    data = stream.read(49153)
    if not data or len(data) > 49152:
        raise ValueError('JPEG size')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'JPEG' or image.size != (320, 240):
            raise ValueError('JPEG identity')
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        image.load()

if __name__ == '__main__':
    main()
