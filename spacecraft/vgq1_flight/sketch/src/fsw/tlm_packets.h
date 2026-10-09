// =============================================================================
//  VGQ-1 telemetry packet layouts (user-data field of each TM Space Packet).
//
//  THIS FILE IS THE FLIGHT HALF OF THE TELEMETRY DICTIONARY. The ground half is
//  ground/dssq/dictionary.py; docs/05_Telemetry_and_Command_Dictionary.md is the
//  human-readable ICD. tools/host_test packs known values with these functions and
//  ground/tests/test_cross_check.py decodes them with the ground dictionary, so
//  any divergence between flight and ground fails the build.
//
//  All fields are big-endian ("network order", as CCSDS mandates). Sentinels:
//  i16 0x7FFF / u16 0xFFFF / i32 0x7FFFFFFF = "not available".
//  Pure C++ (no Arduino), so it compiles on the host.
// =============================================================================
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace vgq {

// ---- APID map (11-bit) --------------------------------------------------------
enum Apid : uint16_t {
  APID_HK       = 0x010,   // engineering housekeeping
  APID_RF       = 0x011,   // telecom / radio subsystem
  APID_EVR      = 0x012,   // event reports (variable length)
  APID_CMDVER   = 0x013,   // command verification
  APID_TIMECORR = 0x014,   // SCLK time-correlation sample
  APID_MAG      = 0x020,   // magnetometer science: RM3100 (boom) + BMM150 (Nicla Sense ME)
  APID_ATT      = 0x021,   // attitude: Nicla Sense ME fusion + IMU + TRIAD
  APID_SPEC     = 0x022,   // AS7265X spectrometer
  APID_ENV      = 0x023,   // environment: Nicla Sense Env + Nicla Sense ME
  APID_RFSCAN   = 0x015,   // RF spectrum survey (ambient noise per E22 channel)
  APID_TC       = 0x0C0,   // telecommand packets (uplink)
};

static const int16_t  kNaI16 = 0x7FFF;
static const uint16_t kNaU16 = 0xFFFF;
static const int32_t  kNaI32 = 0x7FFFFFFF;

enum FswMode : uint8_t { MODE_BOOT = 0, MODE_SAFE = 1, MODE_CRUISE = 2, MODE_ENCOUNTER = 3, MODE_TEST = 4 };

// FDIR flag bits (HK.fdir_flags)
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
  FDIR_HIBERNATE    = 1u << 14,   // informational: spacecraft is hibernating (WOR)
  FDIR_RADIO_FAULT  = 1u << 15,   // E22 reports under/over-voltage or over-temperature
};

// Sensor health bits (HK.sensor_health). The SENSORS command enable mask uses the
// instrument bits: NICLA_ME, NICLA_ENV, AS7265X, RM3100_OB, RM3100_IB.
enum SensorBit : uint16_t {
  SNS_NICLA_ME  = 1u << 0,   // ARU link alive (pages read, CRC ok, sequence advancing)
  SNS_ME_QUAT   = 1u << 1,   // BHI260 rotation vector valid
  SNS_ME_ACC    = 1u << 2,
  SNS_ME_GYRO   = 1u << 3,
  SNS_ME_MAG    = 1u << 4,   // BMM150 via BHI260
  SNS_ME_BARO   = 1u << 5,   // BMP390
  SNS_ME_BSEC   = 1u << 6,   // BME688 + BSEC
  SNS_NICLA_ENV = 1u << 7,
  SNS_ENV_TH    = 1u << 8,   // HS4001 temperature / humidity
  SNS_ENV_IAQ   = 1u << 9,   // ZMOD4410 indoor air quality
  SNS_ENV_OAQ   = 1u << 10,  // ZMOD4510 outdoor air quality (NO2, O3)
  SNS_AS7265X   = 1u << 11,
  SNS_RM3100_OB = 1u << 12,  // science magnetometer, boom tip (0x20)
  SNS_RM3100_IB = 1u << 13,  // optional second RM3100, boom mid-point (0x23)
};

struct HkPacket {                 // APID 0x010, 58 octets
  uint8_t  fsw_mode;
  uint16_t sclk_partition;
  uint32_t uptime_s;
  uint8_t  reset_cause;
  uint16_t fdir_flags;
  uint16_t sensor_health;
  uint16_t cmd_accepted;
  uint16_t cmd_rejected;
  uint8_t  cmd_last_opcode;
  uint8_t  cmd_last_status;
  uint16_t tc_frames_ok;
  uint16_t tc_frames_bad;
  uint8_t  farm_state;
  uint8_t  farm_vr;
  uint32_t cmd_loss_timer_s;
  int16_t  bus_temp_cC;           // Nicla Sense ME (BME688) temperature, 0.01 degC
  uint8_t  radio_fault;           // last E22 abnormal-status code (0 none, 1 UV, 2 OV, 3 OT, 4 OV+OT)
  uint16_t radio_fault_reports;   // E22 abnormal-status reports received
  uint16_t loop_max_ms;
  uint16_t loop_overruns;
  uint16_t i2c_errors;
  uint16_t ssr_fill_permille;
  uint16_t ssr_dropped;
  uint16_t ssr_kib;               // SSR capacity actually allocated
  uint16_t vc0_backlog;
  uint16_t vc1_backlog;
  uint16_t vc2_backlog;
  uint8_t  sdls_enabled;          // 1 = uplink authentication active
  uint16_t sdls_auth_fail;        // frames rejected by SDLS (bad MAC / replay / SPI)
  uint32_t sdls_last_sn;          // last authenticated sequence number
};

struct RfPacket {                 // APID 0x011, 38 octets
  uint8_t  air_rate_code;
  uint8_t  tx_power_code;
  uint8_t  channel;
  uint8_t  e22_cfg_ok;
  uint32_t frames_sent;
  uint32_t oid_frames_sent;
  uint32_t tx_airtime_ms_total;
  uint16_t tx_duty_permille;
  uint16_t frame_period_ms;
  uint16_t aux_timeouts;
  int16_t  uplink_rssi_dbm;       // RSSI of last received uplink packet
  int16_t  noise_floor_dbm;       // ambient channel noise (E22 register 0x00)
  uint16_t cltu_ok;
  uint16_t cltu_bad;
  uint16_t bch_corrected;
  uint32_t rx_bytes;
  uint8_t  mcfc;
  uint8_t  last_airtime_ds;       // last frame air time, 0.1 s units (255 = >= 25.5 s)
};

struct CmdVerPacket {             // APID 0x013, 8 octets
  uint8_t  tc_seq;                // N(S) of the carrying frame, 0xFF if bypass
  uint8_t  opcode;
  uint8_t  stage;                 // 1 accepted, 2 executed OK, 3 failed
  uint8_t  error_code;
  uint32_t ground_tag;            // echoed PING tag (RTLT measurement)
};

struct TimeCorrPacket {           // APID 0x014, 10 octets
  uint8_t  ref_mcfc;              // MCFC of the reference frame
  uint32_t sclk_coarse;           // SCLK when its first octet entered the radio
  uint16_t sclk_fine;
  uint8_t  air_rate_code;
  uint16_t ref_airtime_ms;        // measured air time of the reference frame
};

struct MagVector { int32_t bx_nT, by_nT, bz_nT; uint16_t rms_nT; };
struct MagPacket {                // APID 0x020, 46 octets
  uint8_t  nsamples;
  uint8_t  flags;                 // b0 sample taken with PA keyed, b1 OB ok, b2 IB ok, b3 BMM150 ok
  MagVector outboard;             // RM3100 at the boom tip (0x20)
  MagVector inboard;              // optional RM3100 at the boom mid-point (0x23)
  MagVector body;                 // BMM150 inside the Nicla Sense ME
  uint16_t rm3100_cycle_count;
};

struct AttPacket {                // APID 0x021, 37 octets  (all from the Nicla Sense ME)
  uint8_t  flags;                 // b0 quaternion, b1 accel, b2 gyro, b3 magnetometer, b4 TRIAD
  int16_t  acc_mg[3];
  int16_t  gyro_ddps[3];          // 0.1 deg/s
  int16_t  mag_dT[3];             // BMM150, 0.1 uT (latest sample)
  int16_t  q_fus[4];              // BHI260 rotation vector w,x,y,z * 16384
  uint16_t q_fus_acc_mrad;        // BHI260 accuracy estimate of that solution
  int16_t  q_triad[4];            // independent TRIAD solution (gravity + field), w,x,y,z * 16384
};

struct SpecPacket {               // APID 0x022, 114 octets  (AS7265X)
  uint8_t  flags;                 // b0 valid, b1 white lamp, b2 IR lamp, b3 UV lamp
  uint8_t  gain_code;             // 0 = 1x, 1 = 3.7x, 2 = 16x, 3 = 64x
  uint8_t  int_cycles;            // integration = cycles x 2.8 ms
  float    cal_uW_cm2[18];        // 410 .. 940 nm, calibrated
  uint16_t raw[18];               // raw counts, same order
  int8_t   temp_c[3];             // die temperature, device 0 (master), 1, 2
};

struct EnvPacket {                // APID 0x023, 51 octets
  uint16_t flags;                 // b0 HS4001, b1 ZMOD4410, b2 ZMOD4510, b3 BMP390, b4 BSEC, b5 ME T/RH, b6 ME gas
  int16_t  nenv_t_cC;             // Nicla Sense Env (HS4001)
  uint16_t nenv_rh_cpct;
  float    nenv_iaq;              // ZMOD4410
  float    nenv_tvoc_mg_m3;
  float    nenv_eco2_ppm;
  uint16_t nenv_outdoor_aqi;      // ZMOD4510
  float    nenv_no2_ppb;
  float    nenv_o3_ppb;
  uint32_t nme_p_cPa;             // Nicla Sense ME BMP390, 0.01 hPa (= Pa)
  int16_t  nme_t_cC;              // BME688
  uint16_t nme_rh_cpct;
  uint16_t nme_iaq;               // BSEC
  uint16_t nme_iaq_s;             // BSEC static IAQ
  uint32_t nme_co2eq_ppm;
  uint16_t nme_bvoc_cppm;         // 0.01 ppm
  uint32_t nme_gas_ohm;
  uint8_t  nme_accuracy;          // BSEC accuracy 0..3
};

// Packers return the number of octets written (user data only).
size_t pack_hk(const HkPacket& p, uint8_t* out);
size_t pack_rf(const RfPacket& p, uint8_t* out);
size_t pack_cmdver(const CmdVerPacket& p, uint8_t* out);
size_t pack_timecorr(const TimeCorrPacket& p, uint8_t* out);
size_t pack_mag(const MagPacket& p, uint8_t* out);
size_t pack_att(const AttPacket& p, uint8_t* out);
size_t pack_spec(const SpecPacket& p, uint8_t* out);
size_t pack_env(const EnvPacket& p, uint8_t* out);
// RFSCAN: first channel, count, scan duration (ms), noise dBm per channel (int8,
// -128 = no reading). Up to 84 channels (410.125 .. 493.125 MHz).
size_t pack_rfscan(uint8_t first_ch, uint8_t count, uint16_t duration_ms, const int8_t* noise,
                   uint8_t* out);
// EVR: severity (0 DIAG..4 FATAL), 16-bit event id, ASCII text (<= 80 chars).
size_t pack_evr(uint8_t severity, uint16_t event_id, const char* text, uint8_t* out);

static const size_t kMaxPayload = 128;   // largest user-data field (SPEC = 114)

}  // namespace vgq
