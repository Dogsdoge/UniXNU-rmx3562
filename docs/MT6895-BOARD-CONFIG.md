# MT6895 board config for XNU 8796 — 设计要点

> 结论：不需要从零写板级配置。复制 SWEET（高通 SM7150）配置，改少数几个常量即可。
> 依据：真机只读探测 + dipa-studio/ios-sweet 的 0004-sweet-machine-config.patch 分析。

## 为什么能复用 SWEET

SWEET.h 的硬件假设（patch 第 226-270 行）大部分是**通用 ARMv8.2**，MT6895 全部满足：

| 假设 | SWEET | MT6895 真机 | 匹配? |
|---|---|---|---|
| ARMv8.2, 16K 页, 无 PAC | 是 | 是（arm64.nomte，A78/A55） | ✅ |
| GICv3 | 是 | 是（GICv3 dist 0x0C000000） | ✅ |
| NO_MONITOR / NO_ECORE | 是 | 是 | ✅ |
| 架构 timer | 有 | 有（arch_timer @ GIC 27） | ✅ |
| TrustZone 拥有 Group 0 | 是 | 待验证（MTK GZ/ATF） | ⚠️ |
| vtimer INTID | 19（高通非标准） | **11**（标准，GIC 27=PPI 11） | ❌ 必须改 |
| IPI SGI | 1 | IPI0-6（Linux 视图） | ⚠️ 待确认 |

## 必须改的常量（SWEET → MT6895）

| 常量 | SWEET 值 | MT6895 值 | 出处 |
|---|---|---|---|
| `SWEET_VTIMER_INTID` | 19 | **11** | /proc/interrupts: arch_timer @ GIC 27 = PPI 11 |
| `SWEET_IPI_SGI` | 1 | 待确认（可能是 1） | Linux IPI0-6，XNU 只需一个 SGI |
| `GICR_PE_SIZE` | 0x20000 | 0x20000 | GICv3 标准；真机 GICR @ 0x0C040000 size 0x200000 = 16 redistributors × 0x20000 |
| CPU 数 | 8 | 8 | 真机 8 核 ✅ |

## SWEET.c 里要检查的 GIC 初始化逻辑

patch 第 142-160 行的 SWEET 特殊处理：
```c
gicr_write32(GICR_IGROUPR0, 0xFFFFFFFF);   // 全部 Group 1（TrustZone 拥有 Group 0）
gicr_write32(GICR_ISENABLER0, (1U << SWEET_VTIMER_INTID) | (1U << SWEET_IPI_SGI));
```
MT6895 的 GZ（GenieZone）是否也把 Group 0 给 secure world？需要验证。
如果 MTK 的 GIC 配置不同（Group 0 可用），这段要调整。

**验证方法**（真机只读）：
- 读 ATF/GZ 的 GICD_CTLR 配置（需要 EL3 访问，当前 Linux 层看不到）
- 或从 LK 源码里的 GIC 初始化代码推断
- 或先按 SWEET 的方式试，如果 timer 不触发再改

## 建议的 MT6895 板级配置文件

新建 `MT6895.h`（仿 SWEET.h）：
- 全部通用定义照抄（ARM_ARCH_TIMER, __ARM_16K_PG__, HAS_GIC_V3, GIC 寄存器偏移等）
- `SWEET_VTIMER_INTID` → 改名 `MT6895_VTIMER_INTID = 11`
- `SWEET_IPI_SGI` → `MT6895_IPI_SGI = 1`（先假设）
- machine config 加 `MT6895` 到 SUPPORTED_ARM64_MACHINE_CONFIGS
- `-march=armv8.2-a+crypto`（A78/A55 支持）

## 工作量重新评估

之前说"内核 defconfig 是 FUTURE blocker"——现在看，实际改动量是：
- 板级配置：复制 + 改 2-3 个常量（几小时）
- patch series：ios-sweet 的 0036 个补丁直接复用（它们大多是通用的）
- 需要新增的：MT6895 触控驱动（S3910）、UFS extent 层（已有设计稿）
- 真正不确定的：GIC Group 策略（可能要调试）

**结论**：内核这块没有想象中那么难。主要工作是组装（copy + adapt），不是从零写。

## 真机只读验证结果（2026-11-24）

- **CPU MIDR**（/proc/cpuinfo）：
  - CPU0-3: `CPU implementer 0x41, part 0xd05` = **Cortex-A55 ×4**
  - CPU4-7: `part 0xd41` = **Cortex-A78 ×4**
  - ⇒ 拓扑 4+4（A78 prime 与大 A78 同为 0xd41，MIDR 不可区分）
- **ARM 特性**：`aes pmull sha1 sha2 crc32 atomics` → 支持 `-march=armv8.2-a+crypto` ✅
- **arch timer**：GIC IRQ 27 = PPI 11（标准编号）✅ 确认 VTIMER_INTID=11
- **IPI**：IPI0-6（Linux 视图），SGI1 对应 Function call interrupts（IPI1）——XNU 用 SGI1 做 IPI 可能与 Linux 冲突，但 Linux 已被替换，可用

## 待验证（剩余）

1. GICD_TYPER.SecurityExtn — 需 LK/ATF 层，Linux 看不到；先按 SWEET 假设（Group 0 给 TrustZone）试
2. IPI SGI 编号 — 先假设 1，调试时确认
