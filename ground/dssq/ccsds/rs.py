"""CCSDS Reed-Solomon (255,223) codec, E = 16, I = 1, Berlekamp dual basis.

Code definition (CCSDS 131.0-B §4):
  * GF(2^8) with F(x) = x^8 + x^7 + x^2 + x + 1 (0x187)
  * g(x) = prod_{j=112}^{143} (x - alpha^(11 j))   (fcr = 112, prim = 11)
  * dual-basis symbol representation on the link (Annex F transformation)
  * shortened codes via leading virtual zero fill

The decoder is a direct port of the classic Berlekamp-Massey / Chien / Forney
decoder (P. Karn, libfec ``decode_rs``), operating in the conventional basis
after T^-1 conversion. It corrects up to 16 symbol errors per codeword and
reports -1 when it detects an uncorrectable codeword.
"""
from __future__ import annotations

NN = 255
NROOTS = 32
A0 = NN               # log(0)
GFPOLY = 0x187
FCR = 112
PRIM = 11
IPRIM = 116           # prim^-1 mod 255 (11 * 116 = 1276 = 5*255 + 1)

_TAL = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)


def _build_tables():
    alpha_to = [0] * 256
    index_of = [0] * 256
    index_of[0] = A0
    alpha_to[A0] = 0
    sr = 1
    for i in range(NN):
        index_of[sr] = i
        alpha_to[i] = sr
        sr <<= 1
        if sr & 0x100:
            sr ^= GFPOLY
        sr &= NN
    return alpha_to, index_of


ALPHA_TO, INDEX_OF = _build_tables()


def _modnn(x: int) -> int:
    return x % NN


def _build_genpoly():
    gp = [0] * (NROOTS + 1)
    gp[0] = 1
    root = FCR * PRIM
    for i in range(NROOTS):
        gp[i + 1] = 1
        for j in range(i, 0, -1):
            if gp[j] != 0:
                gp[j] = gp[j - 1] ^ ALPHA_TO[_modnn(INDEX_OF[gp[j]] + root)]
            else:
                gp[j] = gp[j - 1]
        gp[0] = ALPHA_TO[_modnn(INDEX_OF[gp[0]] + root)]
        root += PRIM
    return [INDEX_OF[c] for c in gp]


GENPOLY = _build_genpoly()


def _build_dual_tables():
    taltab = [0] * 256
    tal1tab = [0] * 256
    for i in range(256):
        v = 0
        for j in range(8):
            for k in range(8):
                if i & (1 << k):
                    v ^= _TAL[7 - k] & (1 << j)
        taltab[i] = v
        tal1tab[v] = i
    return taltab, tal1tab


TALTAB, TAL1TAB = _build_dual_tables()   # conventional->dual, dual->conventional


def encode_conventional(data: bytes) -> bytes:
    """Return 32 parity symbols (conventional basis) for len(data) <= 223 symbols."""
    parity = [0] * NROOTS
    for d in data:
        feedback = INDEX_OF[d ^ parity[0]]
        if feedback != A0:
            for j in range(1, NROOTS):
                parity[j] ^= ALPHA_TO[_modnn(feedback + GENPOLY[NROOTS - j])]
        parity = parity[1:] + [ALPHA_TO[_modnn(feedback + GENPOLY[0])] if feedback != A0 else 0]
    return bytes(parity)


def encode(data_dual: bytes) -> bytes:
    """Encode dual-basis data (<= 223 octets, shortened). Returns dual-basis parity."""
    if len(data_dual) > NN - NROOTS:
        raise ValueError("RS(255,223): at most 223 data octets")
    conv = bytes(TAL1TAB[b] for b in data_dual)
    return bytes(TALTAB[p] for p in encode_conventional(conv))


def decode_conventional(codeword: bytearray, pad: int) -> int:
    """In-place decode of a (255 - pad)-symbol conventional-basis codeword.

    Returns the number of corrected symbols, or -1 if uncorrectable.
    """
    n = len(codeword)
    assert n == NN - pad
    # --- syndromes (evaluate at alpha^((FCR+i)*PRIM)), Horner form ------------
    s = [codeword[0]] * NROOTS
    for j in range(1, n):
        cj = codeword[j]
        for i in range(NROOTS):
            if s[i] == 0:
                s[i] = cj
            else:
                s[i] = cj ^ ALPHA_TO[_modnn(INDEX_OF[s[i]] + (FCR + i) * PRIM)]
    syn_error = 0
    for i in range(NROOTS):
        syn_error |= s[i]
        s[i] = INDEX_OF[s[i]]
    if not syn_error:
        return 0

    # --- Berlekamp-Massey -------------------------------------------------------
    lam = [0] * (NROOTS + 1)
    lam[0] = 1
    b = [INDEX_OF[x] for x in lam]
    r = 0
    el = 0
    while True:
        r += 1
        if r > NROOTS:
            break
        discr_r = 0
        for i in range(r):
            if lam[i] != 0 and s[r - i - 1] != A0:
                discr_r ^= ALPHA_TO[_modnn(INDEX_OF[lam[i]] + s[r - i - 1])]
        discr_r = INDEX_OF[discr_r]
        if discr_r == A0:
            b = [A0] + b[:NROOTS]
        else:
            t = [0] * (NROOTS + 1)
            t[0] = lam[0]
            for i in range(NROOTS):
                if b[i] != A0:
                    t[i + 1] = lam[i + 1] ^ ALPHA_TO[_modnn(discr_r + b[i])]
                else:
                    t[i + 1] = lam[i + 1]
            if 2 * el <= r - 1:
                el = r - el
                b = [A0 if x == 0 else _modnn(INDEX_OF[x] - discr_r + NN) for x in lam]
            else:
                b = [A0] + b[:NROOTS]
            lam = t

    lam_idx = [INDEX_OF[x] for x in lam]
    deg_lambda = 0
    for i in range(NROOTS + 1):
        if lam_idx[i] != A0:
            deg_lambda = i

    # --- Chien search -------------------------------------------------------------
    reg = [0] + lam_idx[1:]
    roots, locs = [], []
    k = IPRIM - 1
    for i in range(1, NN + 1):
        q = 1
        for j in range(deg_lambda, 0, -1):
            if reg[j] != A0:
                reg[j] = _modnn(reg[j] + j)
                q ^= ALPHA_TO[reg[j]]
        if q == 0:
            roots.append(i)
            locs.append(k)
            if len(roots) == deg_lambda:
                break
        k = _modnn(k + IPRIM)
    if deg_lambda != len(roots):
        return -1

    # --- Forney: error evaluator and magnitudes ------------------------------------
    deg_omega = deg_lambda - 1
    omega = [A0] * (NROOTS + 1)
    for i in range(deg_omega + 1):
        tmp = 0
        for j in range(i, -1, -1):
            if s[i - j] != A0 and lam_idx[j] != A0:
                tmp ^= ALPHA_TO[_modnn(s[i - j] + lam_idx[j])]
        omega[i] = INDEX_OF[tmp]

    for j in range(len(roots) - 1, -1, -1):
        num1 = 0
        for i in range(deg_omega, -1, -1):
            if omega[i] != A0:
                num1 ^= ALPHA_TO[_modnn(omega[i] + i * roots[j])]
        num2 = ALPHA_TO[_modnn(roots[j] * (FCR - 1) + NN)]
        den = 0
        for i in range(min(deg_lambda, NROOTS - 1) & ~1, -1, -2):
            if lam_idx[i + 1] != A0:
                den ^= ALPHA_TO[_modnn(lam_idx[i + 1] + i * roots[j])]
        if den == 0:
            return -1
        if locs[j] < pad:
            return -1                      # error located in virtual fill => decoding failure
        if num1 != 0:
            codeword[locs[j] - pad] ^= ALPHA_TO[
                _modnn(INDEX_OF[num1] + INDEX_OF[num2] + NN - INDEX_OF[den])]
    return len(roots)


def decode(codeblock_dual: bytes, pad: int):
    """Decode a dual-basis codeblock of 255 - pad octets.

    Returns (corrected_codeblock_dual, n_corrected) with n_corrected == -1 for an
    uncorrectable codeblock (in which case the input is returned unchanged).
    """
    cw = bytearray(TAL1TAB[x] for x in codeblock_dual)
    nerr = decode_conventional(cw, pad)
    if nerr < 0:
        return bytes(codeblock_dual), -1
    out = bytes(TALTAB[x] for x in cw)
    # Defensive re-check: a successful decode must reproduce a valid codeword.
    if nerr > 0 and encode(out[:len(out) - NROOTS]) != out[len(out) - NROOTS:]:
        return bytes(codeblock_dual), -1
    return out, nerr
