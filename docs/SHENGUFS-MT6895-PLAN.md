# SHENGUFS-MT6895-PLAN —— ShengUfs 适配 MT6895 的落地方案

> 日期：2026-11-24 · 基于 UniXNU 官方 ufs.md + ShengUfs 源码分析
> 这是"UEFI 地基"路线上存储部分的最后一块硬骨头

## 1. 现状

- UniXNU 官方 ShengUfs 是 **SM8550（高通）专用**：UFS host 0x1d84000/0x3000、PHY 0x1d80000、
  GCC 0x100000、apps-SMMU stream 0x60（ufshc SMMU 实例）
- MT6895 真机：UFS host **0x112b0000/0x2300**、PHY 未知、时钟/SMMU 需从 DTSI 提取
- 我们的 board.toml 目前没有 ShengUfs 组件，defconfig 里 `CONFIG_XNU_NATIVE_UFS` 未开

## 2. 官方要求的 UFS root 配置（ufs.md）

```toml
[components] include: "ShengUfs"
[kernel] archives: "component:ShengUfs"
[userdata] first_lba / last_lba / lba_bytes   # 从 GPT
[payload.adt] root_match_volume_uuid          # 从选中的 APFS 卷
defconfig: CONFIG_XNU_NATIVE_UFS=y
kernel_args: native-ufs-root=1
```

## 3. 我们的方案与官方的差异

官方 ShengUfs = GPT 分区（userdata）→ window → IOMedia → APFS。
我们的方案 = **pinned 文件的 extent 表** → 映射到裸块 → IOMedia → APFS（DSU 裸块法）。

⇒ 不能用现成 ShengUfs，必须 fork + 改造：
1. **硬件层**：controller base 0x1d84000 → 0x112b0000；PHY/clocks/SMMU 从 MT6895 DTSI 提取
2. **布局层**：RootLayout.h 的 GPT/window 解析 → 改成 extent 表解析（已有设计稿：xnu-shengufs-file-root.patch）
3. **发布层**：RootPublication.h 的 IOMedia 发布保持（这是通用 IOKit 逻辑）

## 4. 落地步骤（按依赖顺序）

1. **fork ShengUfs** → `Dogsdoge/ShengUfs-mt6895`（新仓）
2. 改 NativeUfs.cpp 的硬件常量（controller/PHY/clocks/SMMU）为 MT6895 值
   - 来源：`docs/rmx3562-memory-map.json`（46 保留区）+ mt6895.dtsi + Linux 驱动
3. 应用 extent 适配设计稿（patches/xnu-shengufs-file-root.patch 的 3 个插入点）
4. host 工具链：mkshngroot.py（清单生成）+ mkadt.py extent 注入（均已完成）
5. board.toml 加 ShengUfs 组件 + feeds.toml 加组件源
6. defconfig 开 CONFIG_XNU_NATIVE_UFS=y，kernel_args 加 native-ufs-root=1
7. 编译验证 → 真机测试

## 5. 风险

- MT6895 UFS PHY/clock 细节：ShengUfs 的 SMMU stream 0x60 是高通的，MTK 可能不同 → 需从 DTSI 找
  （这是最大未知，跟 VCP 项目的 IOMMU 困境类似，但 Store 路径在 normal world，比 VCP 简单）
- extent 设计稿不是 git-apply ready → 集成时按真实源码重新生成 patch
- 官方 ShengUfs 用 verified-root 校验 APFS 卷签名，我们的镜像是自造的 → 可能需要关 verified-root
  （apollo 的 components 里没有 VerifiedRoot，可参考）

## 6. 结论

这是 UEFI 地基上存储部分的最后硬骨头，但路径清晰：fork + 改硬件常量 + extent 适配，
都有现成参考（ShengUfs 源码、extent 设计稿、LPDDR/内存映射数据）。工作量集中在硬件常量适配和编译调试。
