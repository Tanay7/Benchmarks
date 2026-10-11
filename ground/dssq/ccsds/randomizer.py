"""CCSDS 255-bit pseudo-randomizer, h(x) = x^8 + x^7 + x^5 + x^3 + 1, seed all ones.

Applied to the RS codeblock (never the ASM). XOR is its own inverse, so the same
function randomizes and de-randomizes. First octets: FF 48 0E C0 9A ...
"""


def _sequence() -> bytes:
    sr = 0xFF
    out = bytearray()
    for _ in range(255):
        octet = 0
        for _ in range(8):
            octet = (octet << 1) | ((sr >> 7) & 1)
            fb = ((sr >> 7) ^ (sr >> 4) ^ (sr >> 2) ^ sr) & 1
            sr = ((sr << 1) | fb) & 0xFF
        out.append(octet)
    return bytes(out)


SEQUENCE = _sequence()


def derandomize(data: bytes) -> bytes:
    return bytes(b ^ SEQUENCE[i % 255] for i, b in enumerate(data))


randomize = derandomize
