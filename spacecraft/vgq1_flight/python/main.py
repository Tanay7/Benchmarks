# =============================================================================
#  VGQ-1 spacecraft EGSE (Electrical Ground Support Equipment) — runs on the
#  UNO Q's Linux side (Qualcomm QRB2210) inside Arduino App Lab.
#
#  Roles (all bench / I&T functions, exactly what an EGSE rack does for a real
#  spacecraft before launch):
#    * Archive every CADU the flight computer hands to the radio
#      (egse_data/cadu_YYYYMMDD.bin: [u64 unix_ns][u16 len][CADU]) — decode it
#      later with:  python3 -m dssq.replay --cadu-file <file>
#    * Persist the boot counter and give the MCU its SCLK partition number
#    * Log transmitter-side status (sc_status) and EVRs as JSON lines
#    * "Umbilical" (hardline) commanding: any CLTU hex line dropped into
#      egse_data/hardline_queue.txt is injected straight into the flight
#      command path, bypassing RF (generate with: dssq-cmd --cltu-hex ...)
#    * Print a transmitter-end status board to the App Lab console
#
#  Bridge rules honoured: handlers never call Bridge.call (forbidden by the
#  library); calls are made from the user loop only.
# =============================================================================
import json
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

from arduino.app_utils import App, Bridge

DATA = Path(__file__).resolve().parent / "egse_data"
DATA.mkdir(parents=True, exist_ok=True)
BOOT_FILE = DATA / "boot_count.txt"
QUEUE = DATA / "hardline_queue.txt"

# CCSDS 255-bit randomizer (h(x)=x^8+x^7+x^5+x^3+1), needed to peek at frame headers
def _rand_seq(n=8):
    sr, out = 0xFF, bytearray()
    for _ in range(n):
        o = 0
        for _ in range(8):
            o = (o << 1) | ((sr >> 7) & 1)
            fb = ((sr >> 7) ^ (sr >> 4) ^ (sr >> 2) ^ sr) & 1
            sr = ((sr << 1) | fb) & 0xFF
        out.append(o)
    return bytes(out)

RAND = _rand_seq()
state = {"cadus": 0, "last_status": {}, "boot_pending": None, "evrs": 0,
         "vc_counts": {}, "last_print": 0.0, "partition": None}


def log(msg: str):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} EGSE {msg}", flush=True)


def _day_file(prefix: str, ext: str) -> Path:
    return DATA / f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.{ext}"


def on_cadu(hexstr: str):
    try:
        cadu = bytes.fromhex(hexstr)
    except ValueError:
        log("malformed CADU hex from MCU")
        return
    with open(_day_file("cadu", "bin"), "ab") as f:
        f.write(struct.pack(">QH", time.time_ns(), len(cadu)) + cadu)
    state["cadus"] += 1
    if len(cadu) >= 10 and cadu[:4] == bytes.fromhex("1ACFFC1D"):
        hdr = bytes(b ^ r for b, r in zip(cadu[4:10], RAND))
        vc = (hdr[1] >> 1) & 7
        state["vc_counts"][vc] = state["vc_counts"].get(vc, 0) + 1


def on_status(js: str):
    try:
        st = json.loads(js)
    except json.JSONDecodeError:
        return
    st["utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["last_status"] = st
    with open(_day_file("sc_status", "jsonl"), "a") as f:
        f.write(json.dumps(st) + "\n")


def on_evr(text: str):
    state["evrs"] += 1
    rec = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "evr": text}
    with open(_day_file("evr", "jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    log(f"EVR  {text}")


def on_boot(reset_cause: int):
    n = int(BOOT_FILE.read_text().strip() or 0) + 1 if BOOT_FILE.exists() else 1
    BOOT_FILE.write_text(str(n))
    state["boot_pending"] = n          # answered from the loop (no Bridge.call in handlers)
    log(f"flight computer boot #{n} (reset cause {reset_cause})")


def print_board():
    s = state["last_status"]
    if not s:
        log("waiting for flight software status ...")
        return
    rates = {0: "0.3k", 1: "1.2k", 2: "2.4k", 3: "4.8k", 4: "9.6k", 5: "19.2k", 6: "38.4k", 7: "62.5k"}
    print("+------------------------- VGQ-1 TRANSMITTER (EGSE) -------------------------+")
    print(f"| MODE {s.get('mode','?'):<9} SCLK {s.get('part')}/{s.get('sclk')}  "
          f"FRAMES {s.get('frames')} (OID {s.get('oid')})  MCFC {s.get('mcfc')}")
    print(f"| RADIO ch {s.get('ch')} = {410.125 + s.get('ch', 0):.3f} MHz  air {rates.get(s.get('air'))}  "
          f"pwr code {s.get('pwr')}  TX {'ON ' if s.get('tx') else 'off'}"
          f"{'  ** INHIBITED **' if s.get('inhibit') else ''}")
    print(f"| frame period {s.get('period_ms')} ms  radio busy {s.get('busy_ms')} ms  "
          f"duty {s.get('duty_pct')} %  PA {s.get('pa_c')} C  Vrad {s.get('vrad')} V")
    print(f"| uplink RSSI {s.get('ul_rssi')} dBm  noise {s.get('noise')} dBm  FARM state {s.get('farm')} "
          f"V(R) {s.get('vr')}  CMD acc {s.get('acc')} rej {s.get('rej')}")
    print(f"| FDIR 0x{s.get('fdir', 0):04X}  sensors 0x{s.get('sns', 0):04X}  VC0 {s.get('vc0')} B  "
          f"VC1 {s.get('vc1')} B  VC2 {s.get('vc2')} B  SSR {s.get('ssr_pm', 0) / 10:.1f} %")
    print(f"| EGSE archived {state['cadus']} CADUs  per-VC {state['vc_counts']}  EVRs {state['evrs']}")
    print("+------------------------------------------------------------------------------+", flush=True)


def loop():
    now = time.time()
    if state["boot_pending"] is not None:
        try:
            Bridge.call("set_partition", state["boot_pending"], timeout=5)
            state["partition"] = state["boot_pending"]
            state["boot_pending"] = None
        except Exception as e:  # MCU busy or restarting: retry next loop
            log(f"set_partition failed ({e}); retrying")
    if QUEUE.exists() and QUEUE.stat().st_size:
        lines = QUEUE.read_text().split()
        QUEUE.write_text("")
        for hx in lines:
            if len(hx) > 200:
                log("hardline CLTU too long for the 256-byte RPC buffer (max 100 octets)")
                continue
            try:
                ok = Bridge.call("hl_cltu", hx, timeout=5)
                log(f"hardline CLTU {len(hx)//2} octets -> {'accepted' if ok else 'rejected'}")
            except Exception as e:
                log(f"hardline call failed: {e}")
    if now - state["last_print"] >= 10:
        state["last_print"] = now
        print_board()
    time.sleep(0.2)


Bridge.provide("tm_cadu", on_cadu)
Bridge.provide("sc_status", on_status)
Bridge.provide("sc_evr", on_evr)
Bridge.provide("sc_boot", on_boot)
log(f"VGQ-1 EGSE started, archive at {DATA}")
if BOOT_FILE.exists():   # MCU may have booted before this app: re-send the current partition
    state["boot_pending"] = int(BOOT_FILE.read_text().strip() or 0)
App.run(user_loop=loop)
