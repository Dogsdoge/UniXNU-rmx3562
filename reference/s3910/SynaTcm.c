// SPDX-License-Identifier: BSD-2-Clause-Patent
// Synaptics TouchComm generation 1 host protocol (SynaTcm.h). Behaviour follows linux-sm8450-mainline's synaptics_tcm_spi.c as
// documented there; the implementation is this file's own.
#include "SynaTcm.h"
#include <string.h>

#define MARKER 0xa5
#define PADDING 0x5a
#define HEADER 4u
#define RESPONSE_MS 500u
#define POLL_MS 15u

// Touch report configuration op codes (each but the structural ones is followed by its bit width).
enum {
    OP_END = 0, OP_FOREACH_ACTIVE = 1, OP_FOREACH = 2, OP_FOREACH_END = 3, OP_PAD = 4,
    OP_INDEX = 6, OP_CLASS = 7, OP_X = 8, OP_Y = 9, OP_Z = 10, OP_WX = 11, OP_WY = 12, OP_ACTIVE_COUNT = 24,
};

// The configuration installed when the firmware's carries no coordinates (as mainline does): per active object index 4, class 4,
// x 16, y 16, z 8, x width 12, y width 12 bits.
static const uint8_t kDefaultConfig[] = {
    OP_FOREACH_ACTIVE, OP_INDEX, 4, OP_CLASS, 4, OP_X, 16, OP_Y, 16, OP_Z, 8, OP_WX, 12, OP_WY, 12, OP_FOREACH_END, OP_END,
};

static unsigned le16(const uint8_t *p) {return (unsigned)p[0] | (unsigned)p[1] << 8;}
static unsigned min_u(unsigned a, unsigned b) {return a < b ? a : b;}

int syna_recv(struct syna_tcm *t)
{
    uint8_t *rx = t->rx;
    if (t->bus.read(t->bus.ctx, rx, SYNA_RD_CHUNK)) return SYNA_ERR_BUS;
    memcpy(t->last_header, rx, HEADER);
    if (rx[0] != MARKER) {++t->bad_marker; return SYNA_ERR_PROTO;}
    t->code = rx[1];
    t->payload_len = 0;
    const unsigned len = le16(rx + 2);
    // Idle, busy and continued-read headers carry nothing, whatever their length field says.
    if (t->code == SYNA_STATUS_IDLE || t->code == SYNA_STATUS_BUSY || t->code == SYNA_STATUS_CONTINUED) return 0;
    if (len > SYNA_MAX_PAYLOAD) {++t->too_long; return SYNA_ERR_SIZE;}
    const unsigned total = HEADER + len + 1;   // header, payload, padding
    unsigned have = min_u(len, SYNA_RD_CHUNK - HEADER);
    memcpy(t->payload, rx + HEADER, have);
    if (total <= SYNA_RD_CHUNK) {
        if (rx[total - 1] != PADDING) {++t->bad_padding; return SYNA_ERR_PROTO;}
    } else {
        // The rest of the stream (payload, then the padding byte) in reads that each start {0xa5, 0x03}.
        unsigned rest = total - SYNA_RD_CHUNK;
        while (rest) {
            const unsigned chunk = min_u(rest, SYNA_RD_CHUNK - 2);
            if (t->bus.read(t->bus.ctx, rx, chunk + 2)) return SYNA_ERR_BUS;
            if (rx[0] != MARKER || rx[1] != SYNA_STATUS_CONTINUED) {++t->bad_continued; return SYNA_ERR_PROTO;}
            const unsigned take = min_u(chunk, len - have);
            memcpy(t->payload + have, rx + 2, take);
            have += take;
            rest -= chunk;
            if (!rest && rx[2 + chunk - 1] != PADDING) {++t->bad_padding; return SYNA_ERR_PROTO;}
        }
    }
    t->payload_len = len;
    ++t->messages;
    return 0;
}

int syna_command(struct syna_tcm *t, uint8_t opcode, const uint8_t *payload, unsigned len)
{
    if (len + 3 > sizeof t->cmd) return SYNA_ERR_SIZE;
    t->cmd[0] = opcode;
    t->cmd[1] = (uint8_t)len;
    t->cmd[2] = (uint8_t)(len >> 8);
    if (len) memcpy(t->cmd + 3, payload, len);
    if (t->bus.write(t->bus.ctx, t->cmd, len + 3)) return SYNA_ERR_BUS;
    const uint64_t deadline = t->bus.now_ms(t->bus.ctx) + RESPONSE_MS;
    do {
        t->bus.sleep_ms(t->bus.ctx, POLL_MS);
        const int r = syna_recv(t);
        if (r == SYNA_ERR_PROTO) continue;          // the controller is not talking yet
        if (r) return r;
        if (t->code == SYNA_REPORT_IDENTIFY && opcode == SYNA_CMD_IDENTIFY) return 0;
        if (t->code >= SYNA_REPORT_IDENTIFY) continue;   // a report, not the response
        if (t->code == SYNA_STATUS_OK) return 0;
        if (t->code == SYNA_STATUS_IDLE || t->code == SYNA_STATUS_BUSY) continue;
        return SYNA_ERR_STATUS;
    } while (t->bus.now_ms(t->bus.ctx) < deadline);
    return SYNA_ERR_TIMEOUT;
}

static int config_has_coordinates(const uint8_t *c, unsigned n)
{
    int x = 0, y = 0;
    for (unsigned i = 0; i < n; ++i) {
        const uint8_t op = c[i];
        if (op == OP_END) break;
        if (op == OP_FOREACH_ACTIVE || op == OP_FOREACH || op == OP_FOREACH_END || op == OP_PAD) continue;
        if (op == OP_X) x = 1;
        if (op == OP_Y) y = 1;
        ++i;   // its bit width
    }
    return x && y;
}

static int read_config(struct syna_tcm *t)
{
    const int r = syna_command(t, SYNA_CMD_GET_TOUCH_REPORT_CONFIG, NULL, 0);
    if (r) return r;
    if (!t->payload_len || t->payload_len > sizeof t->config) return SYNA_ERR_PROTO;
    t->config_len = t->payload_len;
    memcpy(t->config, t->payload, t->config_len);
    return 0;
}

int syna_setup(struct syna_tcm *t)
{
    int r = syna_command(t, SYNA_CMD_IDENTIFY, NULL, 0);
    if (r) return r;
    if (t->payload_len < 18) return SYNA_ERR_PROTO;
    t->version = t->payload[0];
    t->mode = t->payload[1];
    memcpy(t->part, t->payload + 2, 16);
    t->part[16] = 0;
    if (t->mode != SYNA_MODE_APPLICATION) return SYNA_ERR_MODE;   // no firmware download here: the S3910 boots from its flash
    if ((r = syna_command(t, SYNA_CMD_GET_APP_INFO, NULL, 0))) return r;
    // Application info: eight LE16 fields, a 16-byte customer config id, then max x, max y, max objects (offsets 32, 34, 36).
    if (t->payload_len < 46) return SYNA_ERR_PROTO;
    t->max_x = le16(t->payload + 32);
    t->max_y = le16(t->payload + 34);
    t->max_objects = le16(t->payload + 36);
    if (!t->max_objects) t->max_objects = 10;
    if (t->max_objects > SYNA_MAX_OBJECTS) t->max_objects = SYNA_MAX_OBJECTS;
    if (!t->max_x || !t->max_y) return SYNA_ERR_CONFIG;
    if ((r = read_config(t))) return r;
    if (!config_has_coordinates(t->config, t->config_len)) {
        t->config_replaced = 1;
        if ((r = syna_command(t, SYNA_CMD_SET_TOUCH_REPORT_CONFIG, kDefaultConfig, sizeof kDefaultConfig))) return r;
        if ((r = read_config(t))) return r;
        if (!config_has_coordinates(t->config, t->config_len)) return SYNA_ERR_CONFIG;
    }
    const uint8_t report = SYNA_REPORT_TOUCH;
    return syna_command(t, SYNA_CMD_ENABLE_REPORT, &report, 1);
}

// Bits [offset, offset + bits) of the payload, least significant first, bytes in order.
static uint32_t field(const uint8_t *p, unsigned bytes, unsigned offset, unsigned bits)
{
    if (!bits || bits > 32 || offset + bits > bytes * 8) return 0;
    uint32_t v = 0;
    for (unsigned i = 0; i < bits; ++i) v |= (uint32_t)((p[(offset + i) >> 3] >> ((offset + i) & 7)) & 1) << i;
    return v;
}

int syna_parse_touch(struct syna_tcm *t)
{
    const uint8_t *c = t->config;
    const unsigned n = t->config_len, payloadBits = t->payload_len * 8;
    unsigned i = 0, offset = 0, loop = 0, loopBits = 0, object = 0, seen = 0, active = 0;
    int activeOnly = 0, haveActive = 0, haveIndex = 0;
    memset(t->objects, 0, sizeof t->objects);
    while (i < n) {
        const uint8_t op = c[i++];
        if (op == OP_END) break;
        if (op == OP_FOREACH_ACTIVE || op == OP_FOREACH) {
            activeOnly = op == OP_FOREACH_ACTIVE;
            loop = i;
            object = seen = 0;
            haveIndex = 0;
            loopBits = 0;
            unsigned j = i;
            for (; j < n && c[j] != OP_FOREACH_END && c[j] != OP_END; ++j)
                if (c[j] != OP_PAD && j + 1 < n) loopBits += c[++j];
            // An active-object block with no count and no room for one object is skipped.
            if (activeOnly && !haveActive && offset + loopBits > payloadBits) i = (j < n && c[j] == OP_FOREACH_END) ? j + 1 : j;
            continue;
        }
        if (op == OP_FOREACH_END) {
            ++seen;
            if (!activeOnly) {
                if (++object < t->max_objects) i = loop;
            } else if (haveActive ? seen < active : (seen < t->max_objects && offset + loopBits <= payloadBits)) {
                if (!haveIndex) object = seen;   // no index field: the objects come in order
                i = loop;
            }
            continue;
        }
        if (op == OP_PAD) {offset = (offset + 7) & ~7u; continue;}
        if (i >= n || offset >= payloadBits) break;
        const unsigned bits = c[i++];
        const uint32_t v = field(t->payload, t->payload_len, offset, bits);
        offset += bits;
        switch (op) {
        case OP_INDEX:
            if (v >= t->max_objects || v >= SYNA_MAX_OBJECTS) return SYNA_ERR_CONFIG;
            object = v;
            haveIndex = 1;
            break;
        case OP_ACTIVE_COUNT:
            active = min_u(v, t->max_objects);
            haveActive = 1;
            if (!active) return 0;
            break;
        case OP_CLASS: t->objects[object].status = v; break;
        case OP_X: t->objects[object].x = v; break;
        case OP_Y: t->objects[object].y = v; break;
        case OP_Z: t->objects[object].z = v; break;
        case OP_WX: t->objects[object].wx = v; break;
        case OP_WY: t->objects[object].wy = v; break;
        default: break;   // timestamp, gestures, grip: not used
        }
    }
    ++t->reports;
    return 0;
}
