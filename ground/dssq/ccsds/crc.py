"""CRC-16 for the CCSDS Frame Error Control Field (FECF).

Generator x^16 + x^12 + x^5 + 1 (0x1021), preset all ones, no reflection, no
final XOR (CCSDS 132.0-B-3 §4.1.6 / 232.0-B-4 §4.1.4). Check: b"123456789" -> 0x29B1.
"""

def _make_table():
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
        table.append(crc & 0xFFFF)
    return table


_TABLE = _make_table()


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc
