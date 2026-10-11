// =============================================================================
//  VGQ-1 telemetry packet layouts (user-data field of each TM Space Packet), v3.
//
//  THIS FILE IS THE FLIGHT HALF OF THE TELEMETRY DICTIONARY. The ground half is
//  ground/dssq/dictionary.py; docs/05_Telemetry_and_Command_Dictionary.md is the
//  human-readable ICD. tools/host_test packs known values with these functions and
//  ground/tests/test_cross_check.py decodes them with the ground dictionary, so
//  any divergence between flight and ground fails the build.
//
//  Every TM packet (CCSDS 133.0-B-2): 6-octet primary header (version 0, type 0,
//  secondary-header flag 1, APID, sequence flags 11, 14-bit per-APID count) + 6-octet
//  CUC secondary header (4 coarse + 2 fine, implicit P-field 0x2E, 301.0-B-4) + the
//  user data defined here. Field tables: Phase B specification PB-1 (contract 3, 13,
//  24.1, 28).
//
//  All fields are big-endian ("network order", as CCSDS mandates). Sentinels:
//  i8 -128 / i16 0x7FFF / u16 0xFFFF / i32 0x7FFFFFFF / u32 0xFFFFFFFF = "not
//  available" (the ground decodes them to None). The radio API's own "n/a"
//  values (INT16_MIN / INT32_MIN, drivers/radio.h) are mapped with tlm_na_i16() /
//  tlm_na_i32() before they enter a packet.
//  Pure C++ (no Arduino), so it compiles on the host.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace vgq {

// ---- APID map (11-bit) --------------------------------------------------------
enum Apid : uint16_t {
  APID_HK        = 0x010,   // engineering housekeeping (VC0)
  APID_RF        = 0x011,   // telecom subsystem, any radio model (VC0)
  APID_EVR       = 0x012,   // event reports, variable length (VC0)
  APID_CMDVER    = 0x013,   // command verification (VC0)
  APID_TIMECORR  = 0x014,   // SCLK time-correlation sample (VC0)
  APID_RFSCAN    = 0x015,   // RF spectrum survey, ambient noise per channel (VC0)
  APID_MAG       = 0x020,   // magnetometer science: RM3100 (boom) + BMM150 (Nicla Sense ME) (VC1)
  APID_ATT       = 0x021,   // attitude: Nicla Sense ME fusion + IMU + TRIAD (VC1)
  APID_SPEC      = 0x022,   // AS7265X spectrometer, SPEC v3.1 (fsw/spectral.h) (VC1)
  APID_ENV       = 0x023,   // environment: Nicla Sense ME (VC1)
  APID_SPECBURST = 0x024,   // AS7265X burst samples (fsw/spectral.h pack_specburst) (VC1)
  APID_CFDP      = 0x030,   // one CFDP PDU (ccsds/cfdp.h) per packet (VC3, never recorded)
  APID_TC        = 0x0C0,   // telecommand packets (uplink)
};

// Per-APID sequence counters: index 0..11 in the order HK, RF, EVR, CMDVER, TIMECORR,
// RFSCAN, MAG, ATT, SPEC, ENV, SPECBURST, CFDP; 0xFF for any other APID (the emitter
// refuses it, so an unknown APID can never borrow another APID's counter).
static const uint8_t kNumApids = 12;
static const uint8_t kApidIndexNone = 0xFF;
uint8_t apid_index(uint16_t apid);

// ---- "Not available" sentinels ---------------------------------------------------
static const int8_t   kNaI8  = -128;
static const int16_t  kNaI16 = 0x7FFF;
static const uint16_t kNaU16 = 0xFFFF;
static const int32_t  kNaI32 = 0x7FFFFFFF;
static const uint32_t kNaU32 = 0xFFFFFFFFUL;
// Radio-API n/a (INT16_MIN / INT32_MIN, drivers/radio.h kRadioNaI16/I32) -> packet n/a.
inline int16_t tlm_na_i16(int16_t v) { return v == INT16_MIN ? kNaI16 : v; }
inline int32_t tlm_na_i32(int32_t v) { return v == INT32_MIN ? kNaI32 : v; }

enum FswMode : uint8_t { MODE_BOOT = 0, MODE_SAFE = 1, MODE_CRUISE = 2, MODE_ENCOUNTER = 3, MODE_TEST = 4 };
// "BOOT" / "SAFE" / "CRUISE" / "ENCOUNTER" / "TEST"; "?" for any other value.
const char* fsw_mode_name(uint8_t mode);

// FDIR flag bits (HK.fdir_flags) - unchanged from v2.
enum FdirBit : uint16_t {
  FDIR_RADIO_CFG    = 1u << 0,
  FDIR_AUX_TIMEOUT  = 1u << 1,
  FDIR_PA_OVERTEMP  = 1u << 2,
  FDIR_AVI_OVERTEMP = 1u << 3,
  FDIR_CMD_LOSS     = 1u << 4,
  FDIR_I2C_BUS      = 1u << 5,
  FDIR_SENSOR_LOST  = 1u << 6,
  FDIR_LOOP_OVERRUN = 1u << 7,
  FDIR_VC_CONGEST   = 1u << 8,
  FDIR_LOW_BUS_V    = 1u << 9,
  FDIR_FARM_LOCKOUT = 1u << 10,
  FDIR_TX_INHIBIT   = 1u << 11,
  FDIR_RATE_REVERT  = 1u << 12,
  FDIR_SDLS_AUTH    = 1u << 13,   // authentication failures seen on the uplink
  FDIR_HIBERNATE    = 1u << 14,   // informational: spacecraft is hibernating (beacon only)
  FDIR_RADIO_FAULT  = 1u << 15,   // E22-400T37S abnormal-status report only (contract 3.7)
};

// Sensor health bits (HK.sensor_health; contract 3.7, PB-1.15). The SENSORS command
// enable mask uses the instrument bits only (kSensorInstrumentMask).
enum SensorBit : uint16_t {
  SNS_NICLA_ME  = 1u << 0,   // ARU link alive (pages read, CRC ok, sequence advancing)
  SNS_ME_QUAT   = 1u << 1,   // BHI260 rotation vector valid
  SNS_ME_ACC    = 1u << 2,
  SNS_ME_GYRO   = 1u << 3,
  SNS_ME_MAG    = 1u << 4,   // BMM150 via BHI260
  SNS_ME_BARO   = 1u << 5,   // BMP390
  SNS_ME_BSEC   = 1u << 6,   // BME688 + BSEC
  SNS_AS7265X   = 1u << 7,   // spectrometer (set from As7265x::ok() at run time)
  SNS_RM3100_OB = 1u << 8,   // science magnetometer, boom tip (0x20)
  SNS_RM3100_IB = 1u << 9,   // optional second RM3100, boom mid-point (0x23)
};
static const uint16_t kSensorInstrumentMask =
    SNS_NICLA_ME | SNS_AS7265X | SNS_RM3100_OB | SNS_RM3100_IB;   // 0x0381

// ---- Packet flag bits ---------------------------------------------------------------
enum MagFlag : uint8_t {          // MAG.flags (PB-1.8)
  MAG_F_PA_KEYED = 0x01,          // a sample was taken while the PA was keyed
  MAG_F_OB       = 0x02,          // outboard RM3100 vector valid
  MAG_F_IB       = 0x04,          // inboard RM3100 vector valid
  MAG_F_BMM150   = 0x08,          // body (BMM150) vector valid
  MAG_F_LAMP     = 0x10,          // an AS7265X lamp was lit (or its state unknown) during a sample
  MAG_F_CAL      = 0x20,          // an onboard MAGCAL calibration is active (MAG itself stays raw)
};
enum AttFlag : uint8_t {          // ATT.flags (PB-1.9, C-21)
  ATT_F_QUAT         = 0x01,      // fusion quaternion valid
  ATT_F_ACC          = 0x02,
  ATT_F_GYRO         = 0x04,
  ATT_F_MAG          = 0x08,      // BMM150 field valid
  ATT_F_TRIAD        = 0x10,      // TRIAD quaternion valid
  ATT_F_MAG_SCALE    = 0x20,      // BMM150 scale read from the BHI260 (0 = firmware fallback)
  ATT_F_TRIAD_RM3100 = 0x40,      // TRIAD used the calibrated RM3100 field (0 = BMM150)
};
enum EnvFlag : uint8_t {          // ENV.flags (PB-1.11)
  ENV_F_PRESSURE = 0x01,          // BMP390 pressure valid
  ENV_F_BSEC     = 0x02,          // BSEC outputs (IAQ, CO2eq, bVOC, accuracy) valid
  ENV_F_TH       = 0x04,          // temperature and/or humidity (the missing one is its sentinel)
  ENV_F_GAS      = 0x08,          // gas resistance valid
};
enum RfFlag : uint8_t {           // RF.flags (PB-1.4) = Radio::rf_flags()
  RF_F_RX_BOOST = 0x01,           // Rx boosted gain verified (SX126x 0x08AC = 0x96)
  RF_F_LBT      = 0x02,           // LBT / CAD before every transmission
};

// CMDVER tc_seq values that are not an N(S) (PB-1.6).
static const uint8_t kCmdVerSeqBypass = 0xFF;   // Type-BD frame (or the hardline)
static const uint8_t kCmdVerSeqStored = 0xFE;   // dispatched by the stored-sequence table

// ---- Fixed-length packets ---------------------------------------------------------------
struct HkPacket {                 // APID 0x010, 78 octets (PB-1.3; offsets in comments)
  uint8_t  fsw_mode;              //  0 FswMode
  uint16_t sclk_partition;        //  1 EGSE boot counter = SDLS TM epoch, 0 = none yet
  uint32_t uptime_s;              //  3 SCLK seconds
  uint8_t  reset_cause;           //  7 0 POWER-ON 1 PIN 2 SOFTWARE 3 WATCHDOG 4 BROWNOUT 255 UNKNOWN
  uint16_t fdir_flags;            //  8 FdirBit
  uint16_t sensor_health;         // 10 SensorBit
  uint16_t cmd_accepted;          // 12
  uint16_t cmd_rejected;          // 14
  uint8_t  cmd_last_opcode;       // 16
  uint8_t  cmd_last_status;       // 17 CmdError
  uint16_t tc_frames_ok;          // 18 passed FECF + SDLS
  uint16_t tc_frames_bad;         // 20
  uint8_t  farm_state;            // 22 1 OPEN 2 WAIT 3 LOCKOUT
  uint8_t  farm_vr;               // 23
  uint32_t cmd_loss_timer_s;      // 24
  int16_t  bus_temp_cC;           // 28 Nicla Sense ME (BME688) temperature, 0.01 degC
  uint8_t  radio_fault;           // 30 0 none, 1..4 T37S abnormal status, 16..19 SX126x RadioFault
  uint16_t radio_fault_reports;   // 31 T37S reports + SX126x FAULT events (saturating)
  uint16_t loop_max_ms;           // 33 reset after each HK
  uint16_t loop_overruns;         // 35 passes > 250 ms
  uint16_t i2c_errors;            // 37
  uint16_t ssr_fill_permille;     // 39
  uint16_t ssr_dropped;           // 41 overwrite + export-pause drops, saturated (C-06)
  uint16_t ssr_kib;               // 43 SSR capacity actually allocated
  uint16_t vc0_backlog;           // 45 octets
  uint16_t vc1_backlog;           // 47
  uint16_t vc2_backlog;           // 49
  uint16_t vc3_backlog;           // 51
  uint8_t  sdls_tc_service;       // 53 0 NONE 1 AUTH 2 AE (Sdls::service())
  uint8_t  sdls_tm_service;       // 54 0 NONE 2 AE (SdlsTm::service())
  uint16_t sdls_auth_fail;        // 55 TC frames rejected by SDLS
  uint32_t sdls_last_sn;          // 57 last accepted TC SN
  uint16_t sdls_tm_epoch;         // 61 0 = none yet
  uint8_t  sdls_tm_state;         // 63 0 OFF 1 PROTECTED 2 WAITING FOR EPOCH 3 UNPROTECTED (null SA)
  uint8_t  cfdp_state;            // 64 0 IDLE 1 SENDING SSR 2 RELAYING EGSE FILE
  uint32_t cfdp_seq;              // 65 current/last transaction, 0 = none since boot
  uint16_t cfdp_progress_permille;// 69
  uint8_t  seq_state;             // 71 SeqState (autonomy.h)
  uint8_t  seq_count;             // 72 entries pending
  uint16_t seq_next_dt_s;         // 73 0xFFFF none / not armed, saturates 0xFFFE
  uint8_t  mag_bist;              // 75 mag_bist_hk() (autonomy.h)
  uint8_t  magcal_state;          // 76 b0 OB calibration active, b1 IB
  uint8_t  beacon_level;          // 77 BeaconLevel (autonomy.h)
};

struct RfPacket {                 // APID 0x011, 49 octets (PB-1.4)
  uint8_t  radio_model;           //  0 1 E22-400T33D 2 E22-400T37S 3 SX126x
  uint8_t  air_rate_code;         //  1 E22 REG0[2:0] / SX126x profile 0..7
  uint8_t  tx_power_code;         //  2 0..3, meaning per model
  uint8_t  channel;               //  3 f = 410.125 + CH MHz
  uint8_t  radio_cfg_ok;          //  4 Radio::config_ok()
  uint8_t  framing;               //  5 0 TM, 1 USLP
  uint32_t frames_sent;           //  6
  uint32_t oid_frames_sent;       // 10
  uint32_t tx_airtime_ms_total;   // 14
  uint16_t tx_duty_permille;      // 18 60 s window
  uint16_t frame_period_ms;       // 20 saturates at 65535
  uint16_t aux_timeouts;          // 22 TX_TIMEOUT events (all models)
  int16_t  uplink_rssi_dbm;       // 24 last uplink packet, n/a 0x7FFF
  int16_t  uplink_snr_cdb;        // 26 0.01 dB, SX126x only, n/a 0x7FFF
  int32_t  uplink_freq_err_hz;    // 28 raw, SX126x only, n/a 0x7FFFFFFF
  int16_t  noise_floor_dbm;       // 32 n/a 0x7FFF
  uint16_t cltu_ok;               // 34
  uint16_t cltu_bad;              // 36
  uint16_t bch_corrected;         // 38
  uint32_t rx_bytes;              // 40
  uint8_t  mcfc;                  // 44 next TM MCFC (kept in USLP mode)
  uint8_t  last_airtime_ds;       // 45 0.1 s, saturates at 25.5 s
  uint16_t lbt_busy;              // 46 transmissions deferred by LBT/CAD
  uint8_t  flags;                 // 48 RfFlag
};

struct CmdVerPacket {             // APID 0x013, 8 octets
  uint8_t  tc_seq;                // N(S) of the carrying AD frame, kCmdVerSeqBypass, kCmdVerSeqStored
  uint8_t  opcode;
  uint8_t  stage;                 // 2 executed, 3 failed (CmdStage)
  uint8_t  error_code;            // CmdError
  uint32_t ground_tag;            // echoed PING tag (RTLT measurement)
};

struct TimeCorrPacket {           // APID 0x014, 10 octets
  uint8_t  ref_mcfc;              // TM: MCFC of the reference frame; USLP: VCF count of a VC0 frame
  uint32_t sclk_coarse;           // SCLK when the PA was keyed for the reference frame
  uint16_t sclk_fine;
  uint8_t  air_rate_code;
  uint16_t ref_airtime_ms;        // measured busy time of the reference frame
};

struct MagVector { int32_t bx_nT, by_nT, bz_nT; uint16_t rms_nT; };
struct MagPacket {                // APID 0x020, 46 octets (layout unchanged)
  uint8_t  nsamples;
  uint8_t  flags;                 // MagFlag
  MagVector outboard;             // RM3100 at the boom tip (0x20)
  MagVector inboard;              // optional RM3100 at the boom mid-point (0x23)
  MagVector body;                 // BMM150 inside the Nicla Sense ME
  uint16_t rm3100_cycle_count;
};

struct AttPacket {                // APID 0x021, 37 octets (layout unchanged)
  uint8_t  flags;                 // AttFlag
  int16_t  acc_mg[3];
  int16_t  gyro_ddps[3];          // 0.1 deg/s
  int16_t  mag_dT[3];             // BMM150, 0.1 uT (latest sample)
  int16_t  q_fus[4];              // BHI260 rotation vector w,x,y,z * 16384
  uint16_t q_fus_acc_mrad;        // BHI260 accuracy estimate of that solution
  int16_t  q_triad[4];            // independent TRIAD solution (gravity + field), w,x,y,z * 16384
};

struct EnvPacket {                // APID 0x023, 24 octets (Nicla Sense ME only, PB-1.11)
  uint8_t  flags;                 //  0 EnvFlag
  uint32_t p_cPa;                 //  1 BMP390 pressure, 0.01 hPa (= Pa), n/a 0xFFFFFFFF
  int16_t  t_cC;                  //  5 BME688 temperature, 0.01 degC, n/a 0x7FFF
  uint16_t rh_cpct;               //  7 0.01 % (the BHI260 delivers whole %RH), n/a 0xFFFF
  uint16_t iaq;                   //  9 BSEC, n/a 0xFFFF
  uint16_t iaq_s;                 // 11 BSEC static IAQ, n/a 0xFFFF
  uint32_t co2eq_ppm;             // 13 n/a 0xFFFFFFFF
  uint16_t bvoc_cppm;             // 17 0.01 ppm, n/a 0xFFFF
  uint32_t gas_ohm;               // 19 n/a 0xFFFFFFFF
  uint8_t  accuracy;              // 23 BSEC 0..3 (0 when ENV_F_BSEC is clear)
};

static const size_t kHkLen       = 78;
static const size_t kRfLen       = 49;
static const size_t kCmdVerLen   = 8;
static const size_t kTimeCorrLen = 10;
static const size_t kMagLen      = 46;
static const size_t kAttLen      = 37;
static const size_t kEnvLen      = 24;
static const size_t kEvrHdrLen   = 3;     // severity u8 | event_id u16
static const size_t kEvrTextMax  = 80;    // characters (pack_evr truncates)
static const size_t kRfscanHdrLen = 4;    // first_ch u8 | count u8 | duration_ms u16
static const uint8_t kRfscanMaxChannels = 84;   // CH 0..83 (410.125 .. 493.125 MHz)

// Largest user-data field of any APID: the CFDP Metadata PDU with two 64-character
// names (ccsds::kCfdpMaxPduLen = 146). datalink.h static_asserts that SPEC v3.1 (129),
// SPECBURST (<= 124) and every CFDP PDU fit. Largest Space Packet = 12 + 146 = 158
// octets = one TM-AE frame data field.
static const size_t kMaxPayload = 146;

// Packers write the user data only and return the octet count. `out` must hold the
// packet's length (k*Len above); a kMaxPayload buffer always suffices.
size_t pack_hk(const HkPacket& p, uint8_t* out);         // kHkLen
size_t pack_rf(const RfPacket& p, uint8_t* out);         // kRfLen
size_t pack_cmdver(const CmdVerPacket& p, uint8_t* out); // kCmdVerLen
size_t pack_timecorr(const TimeCorrPacket& p, uint8_t* out);   // kTimeCorrLen
size_t pack_mag(const MagPacket& p, uint8_t* out);       // kMagLen
size_t pack_att(const AttPacket& p, uint8_t* out);       // kAttLen
size_t pack_env(const EnvPacket& p, uint8_t* out);       // kEnvLen
// RFSCAN (APID 0x015): first channel, count, scan duration (ms), noise dBm per channel
// (int8, -128 = no reading). count 1..kRfscanMaxChannels; returns 0 (nothing written)
// for count 0, count > 84 or a null pointer.
size_t pack_rfscan(uint8_t first_ch, uint8_t count, uint16_t duration_ms, const int8_t* noise,
                   uint8_t* out);
// EVR (APID 0x012): severity (0 DIAG..4 FATAL), 16-bit event id, ASCII text (truncated
// to kEvrTextMax; a null text sends none). Returns 3 + text length.
size_t pack_evr(uint8_t severity, uint16_t event_id, const char* text, uint8_t* out);

// =============================================================================
//  MCU -> Linux EGSE status notifications (PB-4.1, C-25): two JSON objects built by
//  a pure formatter so their worst-case length is host-tested (<= 480 characters,
//  the per-notification limit of PB-4).
//    sc_status   (1 Hz, 22 keys): mode sclk part epoch tm_state tm_sn frames oid mcfc
//                period_ms busy_ms duty_pct model fr ch code pwr lbt tx inhibit hib beacon
//    sc_status_x (5 s, 29 keys):  sclk ul_rssi ul_snr_cdb noise farm vr acc rej fdir sns
//                vc0 vc1 vc2 vc3 ssr_pm bus_c rf_fault tc_svc tm_svc cfdp cfdp_seq cfdp_pm
//                seq tc_rescued evr_nd cadu_nd emit_ref relay_ref stretch
//  Every value is a JSON number except "mode" (a string from fsw_mode_name()). The
//  i16 radio readings (ul_rssi, ul_snr_cdb, noise) print 32767 when not available
//  (the RF packet's sentinel; the radio API's INT16_MIN is mapped to it), so the
//  EGSE reads them exactly as it reads the RF packet. duty_pct and bus_c are printed
//  with one decimal from integer tenths (no floating-point printf, which newlib-nano
//  builds lack), clamped to 0.0..100.0 and -999.0..999.0; a NaN prints 0.0 / -999.0.
//  Measured worst cases (host test egse_status): see the WS1 report / test output.
// =============================================================================
struct EgseStatus {
  // ---- sc_status (core) ----
  uint8_t  mode;          // FswMode
  uint32_t sclk;          // SCLK seconds
  uint16_t part;          // SCLK partition
  uint16_t epoch;         // SDLS TM epoch (0 = none)
  uint8_t  tm_state;      // SdlsTm::State
  uint32_t tm_sn;         // last TM SN under the current epoch
  uint32_t frames;        // frames transmitted
  uint32_t oid;           // OID frames transmitted
  uint8_t  mcfc;
  uint32_t period_ms;     // frame period in force
  uint32_t busy_ms;       // last measured radio busy time
  float    duty_pct;      // TX duty, 60 s window, %
  uint8_t  model;         // radio model 1..3
  uint8_t  fr;            // downlink framing 0 TM / 1 USLP
  uint8_t  ch, code, pwr; // channel, rate code, power code
  bool     lbt, tx, inhibit, hib;
  uint8_t  beacon;        // BeaconLevel
  // ---- sc_status_x (extended) ----
  int16_t  ul_rssi;       // dBm (n/a: 0x7FFF or INT16_MIN, printed 32767)
  int16_t  ul_snr_cdb;    // 0.01 dB (n/a printed 32767)
  int16_t  noise;         // dBm (n/a printed 32767)
  uint8_t  farm, vr;      // FARM-1 state, V(R)
  uint16_t acc, rej;      // commands accepted / rejected
  uint16_t fdir, sns;     // fdir_flags, sensor_health
  uint16_t vc[4];         // VC0..3 backlog, octets
  uint16_t ssr_pm;        // SSR fill, permille
  float    bus_c;         // bus temperature, degC (NaN -> -999.0)
  uint8_t  rf_fault;      // HK radio_fault
  uint8_t  tc_svc, tm_svc;   // SDLS services
  uint8_t  cfdp;          // HK cfdp_state
  uint32_t cfdp_seq;
  uint16_t cfdp_pm;       // CFDP progress, permille
  uint8_t  seq;           // HK seq_state
  uint32_t tc_rescued;    // uplinks with a LoRa CRC error rescued by BCH/FECF/SDLS
  uint32_t evr_nd;        // sc_evr notifications not delivered (PB-0.4)
  uint32_t cadu_nd;       // tm_cadu copies not delivered
  uint32_t emit_ref;      // packets refused by emit() (unknown APID)
  uint32_t relay_ref;     // CFDP relay PDUs refused (RelayGate::refused())
  uint16_t stretch;       // LoadPlan::stretch_x100
};
static const size_t kEgseStatusCap = 481;   // caller's buffer: 480 characters + NUL
// snprintf into out[0..cap-1]; returns the length (without the NUL), or 0 - and the
// caller sends nothing - for a null/zero buffer or when the text would not fit.
size_t format_sc_status(const EgseStatus& s, char* out, size_t cap);
size_t format_sc_status_x(const EgseStatus& s, char* out, size_t cap);

}  // namespace vgq
