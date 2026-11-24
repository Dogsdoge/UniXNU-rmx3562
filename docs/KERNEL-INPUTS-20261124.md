# KERNEL-INPUTS-20261124 —— kernel 实编译的外部输入清单

> 日期：2026-11-24 · 解决"kernel job 缺外部输入"的最后一块

## 结论：外部输入是公网可下载的 Apple IPSW，不是障碍

## 输入清单（来自 apollo 的 ios-16.5-20F66 profile）

### 1. iPhone X IPSW（公网直链）
```
URL: https://updates.cdn-apple.com/2023SpringFCS/fullrestores/032-85130/6AB0D10A-8AF9-4F6C-8CF5-A1B315CABE88/iPhone10,3,iPhone10,6_16.5_20F66_Restore.ipsw
HEAD 实测 (2026-11-24): HTTP 200, Content-Length=6165698198 (6.17 GiB!) - 不是预估的 2-3GB
提取: KernelCache, DeviceTree, StaticTrustCache, Cryptex1,SystemTrustCache
```
- kernelcache（解压后 arm64 Mach-O slice）sha256: `e6fa75598f47b5a9460a60913e2ac8e4cd6cd60eb475bb31da65b68ffbcfdad5`
- OS cryptex / root 镜像 sha256: `64354ba3...`（apollo 已测）
- trustcache bundle sha256（两个 trustcache 按顺序拼接，67740 字节）

### 2. 我们已拷好的 profile
`UniXNU-rmx3562/os-profiles/ios-16.5-20F66/`（profile.toml 里已有这些 URL + sha256）

### 3. MT6895 板级补丁
`patches/0100-mt6895-timer-ppi.patch`（SWEET_VTIMER_INTID 19→11）已就位

## kernel 编译流程（工具链就绪后）

1. `./unixnu defconfig rmx3562 -D <device>` — 选设备
2. `./unixnu fetch` — 下载 XNU 源码 + IPSW 引用 + Mu_Basecore
3. supply IPSW/kernelcache（从上面直链下载，sha256 校验）
4. `./unixnu build kernel` — 编译 + prelink
5. 输出 `kernel/kernel.macho`

## 当前阻塞点

只有 UniXNU Linux 工具链安装（云端 run #8 正在编译 clang，20-40 分钟）。
工具链就绪后，IPSW 直链 + sha256 都现成，kernel 实编译可立即开始。

## 注意
- IPSW 实测 6.17GB！GitHub runner 14GB 磁盘紧张: IPSW 6.17 + XNU 源码 ~1GB + 编译产物
  可能超限。对策: ① 只下载需要的 IM4P 成员(range 请求, 如 get-base-d22ap.sh 的做法)
  ② 或本地下载解压出 kernelcache 后只传必要文件到 CI (缓存/artifact)
- Apple CDN 公网可达（不需特殊网络）
- sha256 校验保证下载正确
