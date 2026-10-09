"""CCSDS protocol layers used by the DSS-Q ground station.

Every constant here MUST match spacecraft/vgq1_flight/sketch/src/ccsds/ccsds_types.h.
The cross-check in ground/tests/test_cross_check.py verifies this byte-for-byte.
"""

SPACECRAFT_ID = 0x0A7          # 10-bit SCID (private, NOT SANA-registered)

ASM = bytes.fromhex("1ACFFC1D")
RS_N, RS_K, RS_PARITY = 255, 223, 32

TM_FRAME_LEN = 200
TM_PRI_HDR_LEN = 6
TM_OCF_LEN = 4
TM_FECF_LEN = 2
TM_DATA_LEN = TM_FRAME_LEN - TM_PRI_HDR_LEN - TM_OCF_LEN - TM_FECF_LEN   # 188
RS_VIRTUAL_FILL = RS_K - TM_FRAME_LEN                                   # 23
CODEBLOCK_LEN = TM_FRAME_LEN + RS_PARITY                                # 232
CADU_LEN = len(ASM) + CODEBLOCK_LEN                                     # 236

FHP_NO_PACKET_START = 0x7FF
FHP_ONLY_IDLE_DATA = 0x7FE

VC_NAMES = {0: "RT-ENG", 1: "RT-SCI", 2: "PLAYBACK", 7: "IDLE/OID"}

SP_PRI_HDR_LEN = 6
SP_SEC_HDR_LEN = 6
APID_IDLE = 0x7FF
CUC_PFIELD = 0x2E

CLTU_START = bytes.fromhex("EB90")
CLTU_TAIL = bytes.fromhex("C5C5C5C5C5C5C579")
TC_PRI_HDR_LEN = 5
TC_MAX_FRAME_LEN = 64
