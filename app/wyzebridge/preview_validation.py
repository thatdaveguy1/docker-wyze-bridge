from io import BytesIO
from pathlib import Path
from statistics import mean, pstdev


def preview_bytes_are_image(payload: bytes) -> bool:
    if not payload:
        return False

    header = payload[:16]
    return (
        header.startswith(b"\xff\xd8\xff")
        or header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith((b"GIF87a", b"GIF89a"))
        or (len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def _has_vertical_smear(payload: bytes) -> bool:
    try:
        from PIL import Image

        with Image.open(BytesIO(payload)) as image:
            frame = image.convert("L").resize((160, 90))
    except ImportError:
        return False
    except Exception:
        return True

    for top in range(0, frame.height - 20, 10):
        region = frame.crop((0, top, frame.width, min(frame.height, top + 30)))
        pixels = region.load()
        column_deviation = []
        row_deviation = []

        for x in range(region.width):
            column_deviation.append(pstdev(pixels[x, y] for y in range(region.height)))
        for y in range(region.height):
            row_deviation.append(pstdev(pixels[x, y] for x in range(region.width)))

        mean_column_deviation = mean(column_deviation)
        mean_row_deviation = mean(row_deviation)
        flat_column_ratio = sum(1 for value in column_deviation if value < 8) / len(column_deviation)
        if (
            mean_column_deviation <= 1.5
            and mean_row_deviation >= 15
            and flat_column_ratio >= 0.95
            and mean_row_deviation / (mean_column_deviation + 0.01) >= 20
        ):
            return True
    return False


def preview_bytes_are_valid_image(payload: bytes) -> bool:
    return preview_bytes_are_image(payload) and not _has_vertical_smear(payload)


def preview_file_is_image(path: str | Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return preview_bytes_are_valid_image(handle.read())
    except OSError:
        return False