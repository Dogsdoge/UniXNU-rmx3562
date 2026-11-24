# MT6895Pkg UEFI 板级适配清单（pearl → RMX3562）

> 日期：2026-11-24 · 状态：基于 pearl.dsc + 真机只读对比
> 结论：**SoC 级配置完全相同，只需改 framebuffer 相关 3 个值**

## 1. 配置对比表（pearl.dsc vs RMX3562 真机）

| PCD / 配置项 | pearl（红米 Note 12T Pro） | RMX3562（真机） | 动作 |
|---|---|---|---|
| `PcdSystemMemoryBase` | 0x40000000 | 0x40000000 | 保持 ✅ |
| `PcdSystemMemorySize` | 0x300000000 (12GB) | ~12GB (11.1GiB 可用) | 保持 ✅ |
| `PcdCpuVectorBaseAddress` | 0x40C40000 | 同 SoC | 保持 ✅ |
| `PcdPrePiStackBase/Size` | 0x40C00000 / 0x40000 | 同 SoC | 保持 ✅ |
| `PcdUefiMemPoolBase/Size` | 0x40C50000 / 0x7430000 | 需核对我们的 kinfo 保留区起始 | ⚠️ 核对 |
| `PcdMipiFrameBufferWidth` | 1088 | 1088（1080+8 padding，同 SoC 约定） | 保持 |
| `PcdMipiFrameBufferVisibleWidth` | 1080 | 1080 | 保持 ✅ |
| **`PcdMipiFrameBufferHeight`** | **2460** | **2412**（天马 ILI7838A） | **改 2412** ❗ |
| `PcdMipiFrameBufferLayers` | 3 | 3（mblock-14 framebuffer 30MiB，够 3 层） | 保持 |
| **`PcdMipiFrameBufferAddress`** | **0xfe14f000** | **0xfe1af000**（真机 mblock-14） | **改 0xfe1af000** ❗ |
| `PcdMipiFrameBufferPixelFormat` | 1 (RGB) | 同 | 保持 ✅ |
| `PcdMipiFrameBufferOvl0Base` | 0x14002000 | 同 SoC | 保持 ✅ |
| `PcdMipiFrameBufferOvl0_2LBase` | 0x14003000 | 同 SoC | 保持 ✅ |
| `PcdUfsHostControllerBase` | 0x112b0000 | 0x112b0000（DT reg 确认） | 保持 ✅ |
| `PcdKeypadBase` | 0x1c00e000 | 同 SoC（mediatek,kp） | 保持 ✅ |
| `PcdPmifBase` | 0x1c804000 | 同 SoC（MT6363 PMIC） | 保持 ✅ |
| `PEARL_OS_VERSION` (anti-rollback) | 0x18000199 | **必须用 RMX3562 原厂 boot 的 os_version** | **改** ❗ |

## 2. 必须改的 3 处（精简）

1. **`PcdMipiFrameBufferAddress`**: 0xfe14f000 → **0xfe1af000**
2. **`PcdMipiFrameBufferHeight`**: 2460 → **2412**
3. **`PEARL_OS_VERSION`** (build-pearl.sh 里): 改成 RMX3562 原厂 boot 的 os_version
   - 读取方法：从 U 盘字库的 boot_a 备份解析 Android boot header 的 os_version 字段
   - 或从真机 boot_a 读（只读）：`dd if=/dev/block/by-name/boot_a ...` 解析 header

## 3. 需核对的 1 处

- `PcdUefiMemPoolBase`：pearl 是 0x40C50000（DXE heap 到 0x48080000，因 kinfo/minirdump 保留从那开始）
- RMX3562 的 kinfo 保留区在 0x48080000（mblock-10-aee_debug_kinfo）— **相同！**
- ⇒ 大概率也是 0x40C50000 / 0x7430000，保持不变

## 4. 内存映射（PlatformMemoryMapLib）— 必须换

pearl 的 `PlatformMemoryMapLib.c` 是它的 46 个保留区（LK mblock），
RMX3562 的 46 个保留区我们已经采集（`docs/rmx3562-memory-map.json`）。
**要用我们的数据重写这个 C 文件的 `gDeviceMemoryDescriptorEx` 数组**——这是主要工作量。

关键差异（RMX3562 vs pearl）：
- framebuffer: 0xfe1af000 vs 0xfe14f000
- TEE reserved: 0x70000000 85MiB（RMX3562 特有，mblock-21）
- GZ: 0x7f200000 13MiB（RMX3562 特有，mblock-4）
- BL31: 0x48200000 2MiB（RMX3562）

## 5. 适配步骤

1. fork CrackerCat 的 `EDK2-Xiaomi-MediaTek-MT6895`（pearl 分支）
2. `Platform/Xiaomi/pearlPkg` → 复制为 `rmx3562Pkg`，改名所有 pearl 标识
3. 改 `rmx3562.dsc` 的 3 个值（FB 地址、高度、os_version）
4. 用我们的 memory-map.json 重写 `PlatformMemoryMapLib.c`
5. 改 `build-pearl.sh` → `build-rmx3562.sh`（目标名、os_version）
6. stock vendor_boot：需要 RMX3562 自己的（U 盘字库里有）
7. 构建 → boot.img → 刷 boot_a（可回滚）

## 6. 结论

**UEFI 地基适配工作量：主要是"改配置 + 换内存映射表"，不是从零写驱动**——
因为 MT6895Pkg 的 SoC 层（GPIO/UFS/Keypad/PMIC/PlatformPei）是通用的，
只有板级（framebuffer + 内存映射）是机型特定的。

这跟之前"内核板级配置只需改 timer 常量"的结论一致——**MT6895 的 UEFI 和内核移植都是"组装"级别，不是"从零造轮子"**。
