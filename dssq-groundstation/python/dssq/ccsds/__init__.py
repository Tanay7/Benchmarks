"""CCSDS protocol layers used by the DSS-Q ground station.

Every constant here MUST match vgq1-spacecraft/sketch/src/ccsds/ccsds_types.h.
The cross-check in tests/test_cross_check.py verifies this byte-for-byte.
"""

SPACECRAFT_ID = 0x0A7          # 10-bit SCID (private, NOT SANA-registered)

ASM = bytes.fromhex("1ACFFC1D")
RS_K, RS_PARITY = 223, 32       # RS(255,223), E = 16

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

# Virtual channels (ccsds_types.h enum Vc; Phase B PB-1.2). Downlink priority VC0 > VC1 >
# VC2 > VC3 > OID. The same numbers are the USLP VCIDs; the USLP OID frame is VCID 63.
VC_RT_ENG, VC_RT_SCI, VC_PLAYBACK, VC_FILE, VC_IDLE = 0, 1, 2, 3, 7
VC_IDLE_USLP = 63
NUM_DATA_VCS = 4                # VC 0..3 carry packets (kNumDataVcs)
VC_NAMES = {VC_RT_ENG: "RT-ENG", VC_RT_SCI: "RT-SCI", VC_PLAYBACK: "PLAYBACK",
            VC_FILE: "FILE (CFDP)", VC_IDLE: "IDLE/OID", VC_IDLE_USLP: "IDLE/OID (USLP)"}

SP_PRI_HDR_LEN = 6
SP_SEC_HDR_LEN = 6              # CUC 4 + 2 octets, implicit P-field 0x2E (agency epoch)
APID_IDLE = 0x7FF

CLTU_START = bytes.fromhex("EB90")
CLTU_TAIL = bytes.fromhex("C5C5C5C5C5C5C579")
TC_PRI_HDR_LEN = 5
TC_MAX_FRAME_LEN = 64
