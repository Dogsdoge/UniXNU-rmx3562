// SPDX-License-Identifier: BSD-2-Clause-Patent
// Synaptics TouchComm (generation 1) host protocol for the S3910 ("syna,s3910") as linux-sm8450-mainline 568f678 runs it on
// sm8475-realme-senna-b (drivers/input/touchscreen/synaptics_tcm_spi.c). Written from the protocol that driver documents; no code
// is copied (that driver is GPL-2.0). No hardware access here: the caller supplies the SPI transfers, a sleep and a clock.
//
//   command  one SPI write {opcode, length LE16, payload}
//   message  one SPI read of at most 64 bytes (the controller's read chunk), clocking out 0xff: {0xa5, code, length LE16, payload,
//            0x5a}. A longer message continues in reads of at most 64 bytes that start {0xa5, 0x03} and carry the next bytes of
//            payload and padding. Codes 0x00 idle, 0x02 busy and 0x03 continued read carry nothing; 0x01 ok and 0x0f error answer
//            a command; 0x10 and up are reports (0x10 identify, 0x11 touch).
//   touch report  a bit stream described by the touch report configuration: a byte code of {field, bit width} pairs with a
//            repeated per-object block, read with GET_TOUCH_REPORT_CONFIG.
#ifndef SYNA_TCM_H
#define SYNA_TCM_H
#include <stdint.h>
#include <stddef.h>

#define SYNA_RD_CHUNK 64u
#define SYNA_MAX_PAYLOAD 1024u
#define SYNA_MAX_CONFIG 128u
#define SYNA_MAX_OBJECTS 16u

#define SYNA_CMD_IDENTIFY 0x02
#define SYNA_CMD_ENABLE_REPORT 0x05
#define SYNA_CMD_GET_APP_INFO 0x20
#define SYNA_CMD_GET_TOUCH_REPORT_CONFIG 0x25
#define SYNA_CMD_SET_TOUCH_REPORT_CONFIG 0x26
#define SYNA_STATUS_IDLE 0x00
#define SYNA_STATUS_OK 0x01
#define SYNA_STATUS_BUSY 0x02
#define SYNA_STATUS_CONTINUED 0x03
#define SYNA_REPORT_IDENTIFY 0x10
#define SYNA_REPORT_TOUCH 0x11
#define SYNA_MODE_APPLICATION 0x01

// Errors (negative), as the GENI library numbers them where they overlap.
#define SYNA_ERR_BUS (-5)
#define SYNA_ERR_PROTO (-71)
#define SYNA_ERR_SIZE (-90)
#define SYNA_ERR_TIMEOUT (-110)
#define SYNA_ERR_STATUS (-121)
#define SYNA_ERR_MODE (-19)
#define SYNA_ERR_CONFIG (-22)

struct syna_bus {
    void *ctx;
    int (*write)(void *ctx, const uint8_t *tx, size_t len);   // 0 on success
    int (*read)(void *ctx, uint8_t *rx, size_t len);          // clocks out 0xff; 0 on success
    void (*sleep_ms)(void *ctx, unsigned ms);
    uint64_t (*now_ms)(void *ctx);
};

struct syna_object {uint32_t status, x, y, z, wx, wy;};

struct syna_tcm {
    struct syna_bus bus;
    uint8_t rx[SYNA_RD_CHUNK];
    uint8_t cmd[SYNA_MAX_CONFIG + 3];
    uint8_t payload[SYNA_MAX_PAYLOAD];
    unsigned payload_len;
    uint8_t code;
    // Identify report
    uint8_t version, mode;
    char part[17];
    // Application info
    unsigned max_x, max_y, max_objects;
    // Touch report configuration
    uint8_t config[SYNA_MAX_CONFIG];
    unsigned config_len;
    int config_replaced;          // the firmware's configuration had no coordinates; the default one was installed
    struct syna_object objects[SYNA_MAX_OBJECTS];
    // Evidence
    uint32_t messages, reports, bad_marker, bad_padding, bad_continued, too_long;
    uint8_t last_header[4];
};

// One message into t->code / t->payload. 0, or a negative error (SYNA_ERR_PROTO: not a message, e.g. the controller is not talking).
int syna_recv(struct syna_tcm *t);
// Write a command and collect its response; reports meanwhile are dropped (an identify report answers IDENTIFY). Polls every
// 15 ms for at most 500 ms.
int syna_command(struct syna_tcm *t, uint8_t opcode, const uint8_t *payload, unsigned len);
// After reset: IDENTIFY (application mode required), GET_APP_INFO (size, objects), the touch report configuration (the default one
// installed when it carries no coordinates), then ENABLE_REPORT for touch reports.
int syna_setup(struct syna_tcm *t);
// Unpack the touch report in t->payload into t->objects (status 0 = lifted). 0, or SYNA_ERR_CONFIG for an object index out of range.
int syna_parse_touch(struct syna_tcm *t);
#endif
