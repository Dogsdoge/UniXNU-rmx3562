# redwood_s3910.c —— Synaptics S3910 XNU 触控驱动骨架

> 2026-11-24 · 状态：骨架（bring-up 前），未编译、未上真机
> 依据：归档 `docs/TOUCH-IMPLEMENT-PLAN-20261124.md` + `docs/TOUCH-DEEP-DIVE-20261124.md`

## 它是什么

给 UniXNU iOS 16（XNU 8796）在 realme RMX3562 / MT6895 上用的 Synaptics S3910 触控驱动骨架。
协议层来自 `reference/s3910/SynaTcm.c`（TouchComm Gen1 over SPI），传输层照抄 SLNeko 的
`redwood_tcm.c` 并按 MT6895 适配。

设计要点（深挖报告 §0 的 7 处修正，写之前先读）：

- **不上中断**：`kernel_thread_start()` 轮询线程，IRQ135 只做轮询门控
- **SPI mode 用宏 `S3910_SPI_MODE` 可切 0/3**（默认 0，需实测二选一）
- **`SPI_TICKS=3`** → 4.33MHz，不超 S3910 的 5MHz 上限
- **GPIO 加 `+0x10*group` 分组步长**，四根 SPI 脚按数组枚举（不能连续区间遍历）
- **坐标在驱动里归一化到 0..65535**（内核只截断不缩放）
- **`redwood_touch_post()` 只在线程里调**（头注释：not from interrupt context）
- 所有未实测项标 `TODO(BRINGUP):`

## 怎么集成

1. 把 `redwood_s3910.c` 放到 XNU 源码树 `xnu/files/bsd/dev/`（与 `redwood_tcm.c` 同目录）。
2. 在 `xnu/files/bsd/sys/redwood_input.h` 声明区加：`void redwood_s3910_init(void);`
3. 在 `redwood_input.c` 的 `redwood_input_init()` 里仿 `redwood_tcm_init` 加：`redwood_s3910_init();`
4. boot-arg 传 `touchscreen=4`（1=Goodix，2=TD4160，3=Novatek，4=S3910）；
   可选 `touch_poll_us`（默认 8000，钳 250..100000）、`fbfit`（默认 0）。
5. **不需要** LK/UEFI 侧触控初始化（已实证）：驱动自行做 reset(GPIO212) + 电源 + pinctrl + SPI。

## TODO(BRINGUP) 清单（首次 bring-up 必查）

| 项 | 默认假设 | 怎么验证 |
|---|---|---|
| SPI mode | 0 | IDENTIFY 无应答时改 `-DS3910_SPI_MODE=3` 重编 |
| FIFO 深度 | 32B | 命令 >32B 时观察；读走手打通道不走 FIFO |
| SPI 源时钟 | 26MHz | 查 loader clock 配置；TICKS=3 实测频率 |
| CG / 供电 | loader 已开 | printf 分步日志（仿 `redwood_nvt.c:915-932`） |
| CS 物理脚 | GPIO110 | 查 `pinctrl-mtk-mt6895.h`；`reg=<0>`=CS0 低有效 |
| 坐标空间 | max_x/max_y → panel | 打印 GET_APP_INFO 的 max_x/max_y，触摸漂移时先查这里 |
| fbfit | 0 | 显示驱动实际 letterbox 位置未读 |

## 验收方法

1. 编译期：确认无 "macro redefined" 警告；`S3910_SPI_MODE` / `SPI_MODE_BITS` 只定义一次。
2. bring-up 看 printf：
   - `s3910: setup stopped at N ...` → 对照 `s3910_setup_failed()` 分支
     （无消息 = 总线/mode/供电；坏 marker = 换 mode）
   - `s3910: S3910, version N, 1080 x 2412, ...` → 协议通了
   - `s3910: so far N reports ...` → 坐标在往队列送
3. 上屏：`inject.c` 注入 backboardd 后 iOS 触控有响应（只读验证，不写分区）。
4. 真机只读旁证：`/proc/interrupts` 的 `mtk-eint 135 Level touch-00`。

## 状态与红线

未编译、未上真机。全程只读真机；刷 boot_a 由用户亲自执行；
不写 userdata / preloader_raw_a / lk_a / GPT。
