"""Leitura de códigos em imagens de câmera; nenhum arquivo é persistido."""
import io
from threading import BoundedSemaphore
from PIL import Image, ImageOps, UnidentifiedImageError
from pyzbar.pyzbar import decode, ZBarSymbol

MAX_BYTES = 900_000
MAX_PIXELS = 3_000_000
slots = BoundedSemaphore(2)
SYMBOLS = [ZBarSymbol.EAN13, ZBarSymbol.EAN8, ZBarSymbol.UPCA, ZBarSymbol.UPCE,
           ZBarSymbol.CODE128, ZBarSymbol.CODE39, ZBarSymbol.I25]

class CameraImageError(ValueError):
    pass


def read_codes(raw: bytes) -> list[str]:
    if not raw or len(raw) > MAX_BYTES:
        raise CameraImageError('Imagem vazia ou muito grande.')
    try:
        with Image.open(io.BytesIO(raw), formats=['JPEG', 'PNG']) as source:
            if source.width * source.height > MAX_PIXELS or max(source.size) > 2000:
                raise CameraImageError('Resolução da imagem muito alta.')
            image = ImageOps.exif_transpose(source).convert('L')
            codes = set()
            for result in decode(image, symbols=SYMBOLS):
                try:
                    value = result.data.decode('ascii')
                except UnicodeDecodeError:
                    continue
                if 1 <= len(value) <= 80 and value.isprintable() and not any(c.isspace() for c in value):
                    codes.add(value)
            return sorted(codes)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise CameraImageError('Imagem inválida. Tente novamente.') from exc
