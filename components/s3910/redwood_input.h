/*
 * /dev/redwood-touch: touch samples from a driver to the program that turns them into HID
 * events (userland inject.c). The record is the one the redwood kernels use, so the existing
 * injector works unchanged:
 *
 *   write(): one or more records (time is ignored; the kernel stamps them)
 *   read():  blocks until there is at least one record, returns as many whole ones as fit
 *
 * This is the minimal input layer for a kernel with no redwood port: the record, the queue and
 * the device node only. The producer here is bsd/dev/redwood_s3910.c.
 */
#ifndef _SYS_REDWOOD_INPUT_H_
#define _SYS_REDWOOD_INPUT_H_

#include <stdint.h>

#define REDWOOD_TOUCH_DEVICE    "/dev/redwood-touch"

#define REDWOOD_TOUCH_DOWN      1       /* a finger has landed */
#define REDWOOD_TOUCH_MOVE      2
#define REDWOOD_TOUCH_UP        3       /* it has lifted (x, y: where it was last) */
#define REDWOOD_BUTTON_DOWN     4       /* a hardware button; `finger` says which, x and y are 0 */
#define REDWOOD_BUTTON_UP       5

#define REDWOOD_BUTTON_LOCK     1       /* the side button */
#define REDWOOD_BUTTON_VOLUME_UP 2
#define REDWOOD_BUTTON_VOLUME_DOWN 3
#define REDWOOD_BUTTON_HOME     4

struct redwood_touch {
	uint64_t time;                  /* mach_absolute_time() when the sample was posted */
	uint32_t type;                  /* REDWOOD_TOUCH_* or REDWOOD_BUTTON_DOWN/UP */
	uint32_t finger;                /* which finger, from 0 (or which button) */
	uint32_t x, y;                  /* 0 = left/top edge, 65535 = right/bottom edge */
};

#ifdef KERNEL
/* For drivers; not from interrupt context. */
void redwood_touch_post(uint32_t type, uint32_t finger, uint32_t x, uint32_t y);
void redwood_input_init(void);
void redwood_s3910_init(void);         /* boot-arg touchscreen=4: the S3910 on SPI2 */
#endif

#endif /* _SYS_REDWOOD_INPUT_H_ */
