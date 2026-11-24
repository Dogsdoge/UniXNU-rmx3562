/*
 * The touch screen of the realme GT Neo3 (RMX3562): a Synaptics S3910 on SPI2 of the MT6895, as a
 * source of touches for /dev/redwood-touch (sys/redwood_input.h). Only with boot-arg
 * touchscreen=4: every address below belongs to this phone.
 *
 * Unlike the Infinix's TD4160 (redwood_tcm.c), whose firmware the loader pushes in before the
 * kernel starts, the S3910 boots from its own flash already in application mode: nothing is
 * downloaded here. The chip is reset, asked what it is, told which report to send, and from then on
 * it is read. The conversation is TouchComm generation 1 (SynaTcm.c/.h, next to this file); this
 * file is the bus it runs on and the thread that keeps it going.
 *
 * There is no clock, pin or SPI framework in this kernel, so the driver reaches the registers
 * itself:
 *   pins     GPIO110 is the chip select, 111 the data out, 112 the clock and 109 the data in, all at
 *            function 1; GPIO135 is the interrupt line, low while the chip has something to say;
 *            GPIO212 its reset, active low. The MT6895 puts each bank of eight pins in its own
 *            sixteen bytes, so every register is addressed by the group of its pin, never as one
 *            flat bank -- the MT6768 layout this driver was adapted from has only one.
 *   SPI      the controller in FIFO mode, polled; commands go out through the FIFO, reports come in
 *            with the four pins driven by hand, twenty microseconds a byte, the way the loader
 *            reads this family of chips
 *   chip     a message is a 0xa5 marker, a code, two bytes of length, the payload and a 0x5a; the
 *            touch report inside is a bit stream the chip itself describes when asked, so the
 *            payload is parsed by SynaTcm.c, not by a layout written here
 * No interrupt is used: a thread looks at the interrupt line (boot-arg touch_poll_us=N: every N
 * microseconds instead of 8000), the same way the TD4160's driver beside this one does. The line
 * only gates the reads -- nothing is read while it is high, because reading a chip that has nothing
 * to say puts its side of the bus out of step.
 *
 * Register layouts: Linux drivers/spi/spi-mt65xx.c, drivers/pinctrl/mediatek/pinctrl-mt6895.c, and
 * for the chip's own language linux-sm8450-mainline's synaptics_tcm_spi.c as SynaTcm.c documents it.
 *
 * Everything that still has to be measured on the phone is marked TODO(BRINGUP).
 */
#include <kern/clock.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

#include "SynaTcm.h"

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

/*
 * TODO(BRINGUP): the SPI2 controller's clock gate and the panel's 2.8 V and 1.8 V rails are assumed
 * to be left on by the loader: there is no PMIC or clock-gate code here. If syna_setup() never gets
 * an answer, that assumption is the first one to check (and the pinmux below is the second).
 */
#define SPI2_PHYS       0x11012000
#define SPI2_SIZE       0x1000
#define GPIO_PHYS       0x10005000
#define GPIO_SIZE       0x1000

static vm_offset_t spi, gpio;

/*
 * TODO(BRINGUP): the SPI mode is the one thing that cannot be settled from the material at hand.
 * The phone's device tree sets neither spi-cpol nor spi-cpha, which is mode 0 by Linux's default;
 * but the TD4160 beside this driver runs the same controller in mode 3, and the 0xff the protocol
 * clocks out says nothing about the mode (that is a data value, not a clock phase). So: build with
 * -DS3910_SPI_MODE=3 if mode 0 never gets an IDENTIFY answer, and back. Nothing else changes.
 */
#ifndef S3910_SPI_MODE
#define S3910_SPI_MODE   0
#endif
#if S3910_SPI_MODE == 0
#define S3910_CLOCK_IDLE_HIGH   0
#elif S3910_SPI_MODE == 3
#define S3910_CLOCK_IDLE_HIGH   1
#else
#error "S3910_SPI_MODE must be 0 or 3"
#endif

static inline uint32_t
rd(vm_offset_t base, uint32_t at)
{
	return *(volatile uint32_t *)(base + at);
}

static inline void
wr(vm_offset_t base, uint32_t at, uint32_t value)
{
	*(volatile uint32_t *)(base + at) = value;
}

/* ---- the pins ---- */

/*
 * The MT6895 numbers its GPIO registers sixteen bytes apart, one block per eight pins, so a
 * register is its base plus 0x10 times the group the pin falls in (pin / 8), and within the block
 * the pin is bit (pin % 8) of the direction and output registers and four bits (pin % 8) * 4 wide
 * in the mode register.
 */
#define GPIO_DIR_SET(group)     (0x004 + 0x10 * (group))
#define GPIO_DIR_CLR(group)     (0x008 + 0x10 * (group))
#define GPIO_DOUT_SET(group)    (0x104 + 0x10 * (group))
#define GPIO_DOUT_CLR(group)    (0x108 + 0x10 * (group))
#define GPIO_DIN(group)         (0x200 + 0x10 * (group))
#define GPIO_MODE(pin)          (0x300 + ((pin) / 8) * 0x10)
#define GPIO_MODE_SHIFT(pin)    (((pin) % 8) * 4)

#define PIN_MI          109             /* the chip's data out, the controller's in */
#define PIN_CS          110
#define PIN_MO          111
#define PIN_CLK         112
#define PIN_IRQ         135             /* low while a report waits */
#define PIN_RST         212             /* reset, active low */

#define PIN_FUNC_SPI    1
#define PIN_FUNC_GPIO   0

/*
 * The four SPI pins as a list, not as an interval: on this SoC the data-in pin has the lowest number
 * and the clock the highest, so the "for pin = CS to MI" of the MT6768 driver would walk the wrong
 * pins (and stop before the clock).
 */
static const unsigned spi_pins[] = { PIN_MI, PIN_CS, PIN_MO, PIN_CLK };

static inline void
pin_dir(unsigned pin, bool out)
{
	wr(gpio, out ? GPIO_DIR_SET(pin / 8) : GPIO_DIR_CLR(pin / 8), 1u << (pin % 8));
}

static inline void
pin_set(unsigned pin, bool high)
{
	wr(gpio, high ? GPIO_DOUT_SET(pin / 8) : GPIO_DOUT_CLR(pin / 8), 1u << (pin % 8));
}

static inline bool
pin_get(unsigned pin)
{
	return (rd(gpio, GPIO_DIN(pin / 8)) >> (pin % 8)) & 1u;
}

/* Long enough for a pad to settle; one register read is already most of a microsecond. */
static inline void
pin_settle(void)
{
	(void)rd(gpio, GPIO_DIN(PIN_MI / 8));
	(void)rd(gpio, GPIO_DIN(PIN_MI / 8));
}

static void
pin_mode(unsigned pin, uint32_t function)
{
	uint32_t at = GPIO_MODE(pin);
	uint32_t shift = GPIO_MODE_SHIFT(pin);

	wr(gpio, at, (rd(gpio, at) & ~(0xfu << shift)) | (function << shift));
}

static void
pins_to_spi(void)
{
	for (unsigned i = 0; i < sizeof spi_pins / sizeof spi_pins[0]; i++) {
		pin_mode(spi_pins[i], PIN_FUNC_SPI);
	}
	__asm__ volatile ("dsb sy");
}

static void
pins_to_gpio(void)
{
	for (unsigned i = 0; i < sizeof spi_pins / sizeof spi_pins[0]; i++) {
		pin_mode(spi_pins[i], PIN_FUNC_GPIO);
	}
	pin_dir(PIN_CS, true);
	pin_dir(PIN_MO, true);
	pin_dir(PIN_CLK, true);
	pin_dir(PIN_MI, false);
	pin_set(PIN_CS, true);
	pin_set(PIN_CLK, S3910_CLOCK_IDLE_HIGH ? true : false);
	__asm__ volatile ("dsb sy");
}

/*
 * The interrupt line and the reset are plain GPIOs: the line is read, the reset is driven. The line
 * is only ever read as a level from here, so it stays at function 0 -- the EINT block it may
 * normally belong to is not touched.
 */
static void
pins_init(void)
{
	pin_mode(PIN_IRQ, PIN_FUNC_GPIO);
	pin_dir(PIN_IRQ, false);
	pin_mode(PIN_RST, PIN_FUNC_GPIO);
	pin_dir(PIN_RST, true);
	__asm__ volatile ("dsb sy");
}

/*
 * Reset, active low: held for a millisecond, then let go. The loader does not bring this chip up for
 * us (there is no touch code in the UEFI firmware), so the kernel does it here before the first
 * command.
 *
 * TODO(BRINGUP): the release-to-first-command wait is a guess (the time Android's driver allows is
 * not in the material we have). If IDENTIFY times out on a cold boot but works when the chip was
 * already warm, this is the number to raise.
 */
static void
s3910_reset(void)
{
	pin_set(PIN_RST, false);
	delay(1000);
	pin_set(PIN_RST, true);
	delay(20000);
}

/* ---- the SPI controller ---- */

#define SPI_CFG0        0x00
#define SPI_CFG1        0x04
#define SPI_TX_DATA     0x10
#define SPI_RX_DATA     0x14
#define SPI_CMD         0x18
#define SPI_STATUS0     0x1c
#define SPI_PAD_SEL     0x24
#define SPI_CFG2        0x28

#define SPI_CMD_ACT     (1u << 0)
#define SPI_CMD_RST     (1u << 2)
#define SPI_CMD_CPHA    (1u << 8)
#define SPI_CMD_CPOL    (1u << 9)
#define SPI_CMD_TXMSBF  (1u << 12)
#define SPI_CMD_RXMSBF  (1u << 13)
#define SPI_STATUS0_DONE (1u << 0)

/*
 * TODO(BRINGUP): the FIFO's depth is assumed to be 32 bytes, as it is on the MT6768 this code came
 * from. A TouchComm read is up to 64 bytes (SYNA_RD_CHUNK), so reads are not done through the FIFO
 * at all (see s3910_bus_read); commands never exceed 32. If a command is ever shortened, this is
 * the number to confirm against the controller's documentation.
 */
#define SPI_FIFO_BYTES  32

#if S3910_SPI_MODE == 3
#define SPI_MODE_BITS   (SPI_CMD_CPHA | SPI_CMD_CPOL)
#else
#define SPI_MODE_BITS   0u
#endif

/*
 * The clock's half-periods live in CFG2 on this generation of the controller -- Linux calls it
 * "enhance timing" -- while CFG0 keeps only the chip select's setup and hold. Each field counts
 * ticks of the source clock and is stored one less than it counts.
 *
 * TODO(BRINGUP): the source is assumed to be the 26 MHz crystal. Three ticks a half-period is
 * 26 MHz / (2 * 3) = 4.33 MHz, under the 5 MHz the phone's device tree allows. Two ticks would be
 * 6.5 MHz and over it. Confirm the source (the loader's clock setup for SPI2) before trusting the
 * exact rate; the 5 MHz ceiling only has to be respected, not hit.
 */
#define SPI_TICKS       3

/*
 * TODO(BRINGUP): the chip select is assumed to be pad SPI2_A, which PAD_SEL 0 selects and which the
 * device tree's pinmux implies (GPIO110 at function 1). If the controller drives a different pad,
 * PAD_SEL is the number to change -- and if the chip select has to be driven by hand instead, that
 * is the whole of pins_to_gpio() plus a pin_set(PIN_CS) around each transfer.
 */
static void
spi_setup(void)
{
	wr(spi, SPI_CMD, SPI_CMD_RST);
	wr(spi, SPI_CMD, 0);
	wr(spi, SPI_CFG2, ((SPI_TICKS - 1) << 0) | ((SPI_TICKS - 1) << 16));
	wr(spi, SPI_CFG0, ((SPI_TICKS - 1) << 0) | ((SPI_TICKS - 1) << 16));
	wr(spi, SPI_PAD_SEL, 0);
}

static unsigned stalls;

/* One transfer through the FIFO: `length` bytes out of tx (0xff where tx is NULL) and as many in. */
static bool
spi_fifo(const uint8_t *tx, uint8_t *rx, uint32_t length)
{
	uint32_t words = (length + 3) / 4;

	if (length == 0 || length > SPI_FIFO_BYTES) {
		return false;
	}
	spi_setup();
	wr(spi, SPI_CMD, SPI_MODE_BITS | SPI_CMD_TXMSBF | SPI_CMD_RXMSBF);
	wr(spi, SPI_CFG1, ((length - 1) << 16) | ((SPI_TICKS - 1) << 0));

	for (uint32_t word = 0; word < words; word++) {
		uint32_t value = 0;

		for (uint32_t byte = 0; byte < 4; byte++) {
			uint32_t at = word * 4 + byte;

			value |= (uint32_t)(tx && at < length ? tx[at] : 0xff) << (8 * byte);
		}
		wr(spi, SPI_TX_DATA, value);
	}
	wr(spi, SPI_CMD, rd(spi, SPI_CMD) | SPI_CMD_ACT);

	for (uint32_t spin = 0; !(rd(spi, SPI_STATUS0) & SPI_STATUS0_DONE); spin++) {
		if (spin > 200000) {
			return false;
		}
	}
	for (uint32_t word = 0; word < words; word++) {
		uint32_t value = rd(spi, SPI_RX_DATA);

		for (uint32_t byte = 0; byte < 4; byte++) {
			uint32_t at = word * 4 + byte;

			if (rx && at < length) {
				rx[at] = (uint8_t)(value >> (8 * byte));
			}
		}
	}
	return true;
}

/*
 * The same transfer with the four pins driven by hand: the clock idles at the level the mode asks
 * for, the data changes on the edge that leads and is read on the one that follows. Slower, and
 * needs nothing of the controller to be right, which is what makes it the answer when the
 * controller sulks -- and the only way to read more than the FIFO holds in one chip-select cycle.
 */
static void
spi_by_hand(const uint8_t *tx, uint8_t *rx, uint32_t length, uint32_t gap_us)
{
	pins_to_gpio();
	pin_set(PIN_CS, false);
	pin_settle();

	for (uint32_t at = 0; at < length; at++) {
		uint8_t out = tx ? tx[at] : 0xff, in = 0;

		for (int bit = 7; bit >= 0; bit--) {
			uint8_t answer;

			pin_set(PIN_MO, (out >> bit) & 1);
#if S3910_SPI_MODE == 3
			pin_set(PIN_CLK, false);        /* leading edge; the clock idles high */
			pin_settle();
			pin_set(PIN_CLK, true);         /* trailing edge: the chip's bit is on the line */
			pin_settle();
			answer = pin_get(PIN_MI) ? 1 : 0;
#else
			pin_set(PIN_CLK, true);         /* leading edge; the clock idles low */
			pin_settle();
			answer = pin_get(PIN_MI) ? 1 : 0;
			pin_set(PIN_CLK, false);        /* trailing edge */
			pin_settle();
#endif
			in = (uint8_t)((in << 1) | answer);
		}
		if (rx) {
			rx[at] = in;
		}
		if (gap_us) {
			delay(gap_us);
		}
	}
	pin_set(PIN_CS, true);
	pin_settle();
	pins_to_spi();
}

/*
 * Reading is done by hand, with twenty microseconds after every byte: that is the way the loader
 * reads this family of chips, while through the controller -- bytes back to back, and no way to hold
 * the chip select across two FIFO loads -- a 64-byte message would arrive in pieces the chip never
 * meant as separate reads.
 *
 * TODO(BRINGUP): once the FIFO is proven on this controller, a read of up to SPI_FIFO_BYTES could
 * go through it; the protocol's 64-byte chunk cannot, without a way to keep the chip select low
 * across two loads, which this controller's FIFO mode does not offer.
 */
#define READ_GAP_US     20

/* ---- the bus the protocol runs on (SynaTcm.h: struct syna_bus) ---- */

static int
s3910_bus_write(__unused void *ctx, const uint8_t *tx, size_t len)
{
	if (len == 0) {
		return -1;
	}
	if (len > SPI_FIFO_BYTES) {
		/* Longer than the FIFO: one chip-select cycle by hand keeps the frame whole. */
		spi_by_hand(tx, NULL, (uint32_t)len, 0);
		return 0;
	}
	if (spi_fifo(tx, NULL, (uint32_t)len)) {
		return 0;
	}
	stalls++;
	spi_by_hand(tx, NULL, (uint32_t)len, 0);
	return 0;
}

static int
s3910_bus_read(__unused void *ctx, uint8_t *rx, size_t len)
{
	/* The protocol never asks for more than its read chunk, which is 64. */
	if (len == 0 || len > SYNA_RD_CHUNK) {
		return -1;
	}
	spi_by_hand(NULL, rx, (uint32_t)len, READ_GAP_US);      /* clocks out 0xff */
	return 0;
}

static void
s3910_bus_sleep_ms(__unused void *ctx, unsigned ms)
{
	delay(ms * 1000);
}

static uint64_t
s3910_bus_now_ms(__unused void *ctx)
{
	uint64_t ns;

	absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
	return ns / 1000000;
}

/* ---- bringing the chip up ---- */

static struct syna_tcm tcm;

/*
 * A line about the failure, with the numbers that tell the failures apart: no messages at all means
 * the bus is not talking (mode, wiring, power); messages whose marker is wrong means the bits are
 * arriving in the wrong phase (the other SPI mode); a timeout after good messages means the chip is
 * there but does not like the command.
 */
static void
s3910_setup_failed(int error)
{
	printf("s3910: setup stopped at %d after %u messages (%u bad markers, %u bad padding, "
	    "%u bad continuations, %u too long; last header %02x %02x %02x %02x)\n",
	    error, tcm.messages, tcm.bad_marker, tcm.bad_padding, tcm.bad_continued, tcm.too_long,
	    tcm.last_header[0], tcm.last_header[1], tcm.last_header[2], tcm.last_header[3]);
	if (tcm.messages == 0) {
		printf("s3910: nothing came back at all -- check SPI mode (S3910_SPI_MODE), "
		    "the pinmux, the clock gate and the panel's rails\n");
	} else if (tcm.bad_marker != 0) {
		printf("s3910: the bus answers but not with a marker -- try -DS3910_SPI_MODE=3\n");
	}
}

/*
 * The order the protocol asks for, and SynaTcm.c performs: IDENTIFY (0x02), then the chip must
 * already be in application mode (0x01) -- it boots from its own flash, so there is no firmware to
 * download and no way to put it there from this kernel -- then GET_APP_INFO (0x20) for the size and
 * the object count, the touch report configuration (0x25, replaced with the default one by 0x26 if
 * it carries no coordinates), and ENABLE_REPORT (0x05) for touch reports.
 */
static bool
s3910_setup(void)
{
	int error;

	tcm.bus.ctx = NULL;
	tcm.bus.write = s3910_bus_write;
	tcm.bus.read = s3910_bus_read;
	tcm.bus.sleep_ms = s3910_bus_sleep_ms;
	tcm.bus.now_ms = s3910_bus_now_ms;

	printf("s3910: reset, then the protocol\n");
	pins_init();
	pins_to_gpio();                 /* the pins idle before the controller takes them */
	s3910_reset();
	pins_to_spi();
	spi_setup();

	error = syna_setup(&tcm);
	if (error) {
		s3910_setup_failed(error);
		return false;
	}
	printf("s3910: %s, version %u, %u x %u, %u objects, config %u bytes%s\n",
	    tcm.part, tcm.version, tcm.max_x, tcm.max_y, tcm.max_objects, tcm.config_len,
	    tcm.config_replaced ? " (the default one installed)" : "");
	return true;
}

/* ---- what a touch report holds ---- */

#define PANEL_W         1080            /* touchpanel,display-coords */
#define PANEL_H         2412
#define MAX_FINGERS     10              /* inject.c carries no more, and wants the number under 10 */

/*
 * TODO(BRINGUP): the report's own coordinate space. GET_APP_INFO says how far x and y go (max_x,
 * max_y) and that is not necessarily the panel's 1080 x 2412 -- the panel is 4320 x 9648 raw, four
 * times the display, and which of the two the firmware reports is unconfirmed. So the native value
 * is scaled to panel pixels when the two disagree, and the panel-pixel value is what the picture
 * fit below works on. If the first bring-up shows touches cramped or wandering, print max_x/max_y
 * and check this conversion first.
 */
static uint32_t
panel_x(uint32_t x)
{
	if (!tcm.max_x || tcm.max_x == PANEL_W) {
		return x < PANEL_W ? x : PANEL_W - 1;
	}
	x = (uint32_t)((uint64_t)x * PANEL_W / tcm.max_x);
	return x < PANEL_W ? x : PANEL_W - 1;
}

static uint32_t
panel_y(uint32_t y)
{
	if (!tcm.max_y || tcm.max_y == PANEL_H) {
		return y < PANEL_H ? y : PANEL_H - 1;
	}
	y = (uint32_t)((uint64_t)y * PANEL_H / tcm.max_y);
	return y < PANEL_H ? y : PANEL_H - 1;
}

static uint32_t fit;                    /* boot-arg fbfit: how the picture sits on the panel */

/*
 * TODO(BRINGUP): fit 0 (the whole panel is the picture) is the assumption; the display driver's
 * actual placement on this phone has not been read. fit 1 is 1080 x 2338 in the middle of the
 * panel, 37 rows above and below; fit 2 is 1113 x 2412, 16 columns cut off each side. Fit 3 of the
 * TD4160 driver is deliberately absent: its crop goes negative on a panel this shape and wraps
 * around in uint32.
 */
#define PICTURE_H       (PANEL_W * 2436 / 1125)
#define PICTURE_TOP     ((PANEL_H - PICTURE_H) / 2)
#define PICTURE_W       (PANEL_H * 1125 / 2436)
#define PICTURE_CROP    ((PICTURE_W - PANEL_W) / 2)

static uint32_t
picture_x(uint32_t x)
{
	if (fit != 2) {
		return x * 65535 / (PANEL_W - 1);
	}
	return (x + PICTURE_CROP) * 65535 / (PICTURE_W - 1);
}

static uint32_t
picture_y(uint32_t y)
{
	if (fit != 1) {
		return y * 65535 / (PANEL_H - 1);
	}
	if (y <= PICTURE_TOP) {
		return 0;
	}
	y -= PICTURE_TOP;
	return y >= PICTURE_H ? 65535 : y * 65535 / (PICTURE_H - 1);
}

static bool down[MAX_FINGERS];
static unsigned reports, touches;
static uint32_t last_x, last_y;

/*
 * A report turned into what /dev/redwood-touch wants: a landing and a lifting of its own rather than
 * a state to be compared, and the position across the display rather than in the panel's own
 * pixels. A finger the report no longer mentions has lifted.
 *
 * syna_parse_touch() has already unpacked the payload into tcm.objects[], one entry per finger the
 * configuration's index field names (array position = finger number), status 0 for a finger that is
 * not there. A finger gets DOWN the first time it is seen and MOVE after that -- never a MOVE or an
 * UP without a DOWN, which is what the reader on the other side requires.
 */
static void
report(void)
{
	bool seen[MAX_FINGERS] = { false };
	unsigned count = tcm.max_objects;

	if (count > MAX_FINGERS) {
		count = MAX_FINGERS;
	}
	if (count > SYNA_MAX_OBJECTS) {
		count = SYNA_MAX_OBJECTS;
	}
	for (unsigned which = 0; which < count; which++) {
		const struct syna_object *object = &tcm.objects[which];
		uint32_t x, y;

		if (object->status == 0) {
			continue;               /* that one has lifted, or was not in this report */
		}
		x = panel_x(object->x);
		y = panel_y(object->y);
		last_x = x;
		last_y = y;
		seen[which] = true;
		touches++;
		redwood_touch_post(down[which] ? REDWOOD_TOUCH_MOVE : REDWOOD_TOUCH_DOWN, which,
		    picture_x(x), picture_y(y));
		down[which] = true;
	}
	for (unsigned which = 0; which < MAX_FINGERS; which++) {
		if (down[which] && !seen[which]) {
			redwood_touch_post(REDWOOD_TOUCH_UP, which,
			    picture_x(last_x), picture_y(last_y));
			down[which] = false;
		}
	}
	reports++;
}

/* ---- the thread that watches the line ---- */

/*
 * Not from interrupt context: redwood_touch_post() takes a lock and wakes the reader, and the header
 * says so. That is one of the reasons this is a thread and not an interrupt handler -- the other is
 * that the protocol itself is polled (syna_recv reads when asked, nothing pushes).
 */
static void
s3910_thread(__unused void *parameter, __unused wait_result_t result)
{
	uint32_t poll_us = 8000;

	PE_parse_boot_argn("touch_poll_us", &poll_us, sizeof(poll_us));
	PE_parse_boot_argn("fbfit", &fit, sizeof(fit));
	if (poll_us < 250 || poll_us > 100000) {
		poll_us = 8000;
	}

	spi = ml_io_map(SPI2_PHYS, SPI2_SIZE);
	gpio = ml_io_map(GPIO_PHYS, GPIO_SIZE);
	if (!s3910_setup()) {
		printf("s3910: the chip did not come up; the touch screen stays silent\n");
		/*
		 * Returning from a kernel thread would run off its stack. Park instead, and let a
		 * reboot (with another S3910_SPI_MODE, most likely) be the retry.
		 */
		for (;;) {
			delay(1000000);
		}
	}
	printf("s3910: the touch screen's interrupt line is looked at every %u us\n", poll_us);

	for (unsigned tick = 1;; tick++) {
		/*
		 * Nothing is read while the line is high: reading one that has nothing to say puts
		 * its side of the bus out of step, and then the next real report cannot be read
		 * either.
		 */
		while (!pin_get(PIN_IRQ)) {
			int error = syna_recv(&tcm);

			if (error) {
				/* Out of step with the chip: let the next assertion start afresh. */
				break;
			}
			if (tcm.code == SYNA_REPORT_TOUCH) {
				if (syna_parse_touch(&tcm) == 0) {
					report();
				}
			}
		}
		if (tick % (60 * (1000000 / poll_us)) == 0) {
			printf("s3910: so far %u reports and %u finger positions, the last at %u, %u; "
			    "the interrupt line is %s; %u messages, %u bad markers; %u transfers the "
			    "controller would not finish\n",
			    reports, touches, last_x, last_y, pin_get(PIN_IRQ) ? "idle" : "asserted",
			    tcm.messages, tcm.bad_marker, stalls);
		}
		delay(poll_us);
	}
}

void
redwood_s3910_init(void)
{
	uint32_t which = 0;
	thread_t thread;

	/* 4 is this phone; 1, 2 and 3 are the Goodix, the TD4160 and the Novatek beside this. */
	if (!PE_parse_boot_argn("touchscreen", &which, sizeof(which)) || which != 4) {
		return;
	}
	if (kernel_thread_start(s3910_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("s3910: cannot start the thread that reads the touch screen\n");
	}
}
