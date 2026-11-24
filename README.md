# UniXNU-rmx3562

UniXNU iOS 16 port for **realme GT Neo3 (RMX3562) / MediaTek MT6895 (Dimensity 8100)**.

> WORK IN PROGRESS — skeleton stage. No boot image produced yet.

## Goal

Boot iOS 16.5 (XNU 8796.121.2) on the MT6895, using the UniXNU framework:
UEFI (MT6895Pkg) → UniXNU menu → DarwinBoot → XNU 8796 → iOS userland.
Android data preserved; iOS root stored as plaintext in a pinned raw-block region of userdata.

## Why this is feasible

- UniXNU supports an `ios-16.5-20F66` OS profile with a 12-patch xnu-8796 series
  (proven on apollo/SM8250 and raphael/SM8150).
- MT6895 XNU 8796 drivers already exist in the community (SLNeko-XNU, hakios-xaga)
  — same kernel version, no ABI break.
- MT6895 UEFI firmware exists (CrackerCat/EDK2-Xiaomi-MediaTek-MT6895).

## Status

| Item | State |
|---|---|
| iOS 16 profile (os-profiles/ios-16.5-20F66) | ✅ copied from apollo |
| board.toml skeleton | ✅ (values TBD after first firmware build) |
| mt6895-common SoC layer | 🔨 in progress |
| MT6895 DTS | 🔨 TBD |
| XNU drivers (from SLNeko, reformatted for UniXNU) | 🔨 TBD |
| ShengUfs extent layer | 🔨 TBD |
| boot.img build | ❌ not yet |

## Layout

```
board.toml              - board identity + component selection
os-profiles/            - iOS 16.5 profile (xnu-8796 patches), copied from apollo
dts/                    - device tree (TBD)
configs/                - kernel defconfig (TBD)
patches/xnu/            - device-specific XNU patches (TBD)
.github/workflows/      - cloud build (TBD, from raphael)
scripts/                - build helpers (TBD)
```

## References

- Profile/patch series: `Pistachio-Project/UniXNU-apollo`
- Turnkey build flow: `wazihenduan/UniXNU-raphael`
- MT6895 XNU drivers: `ShuiLan114514/SLNeko-XNU`, `lizi600jin/hakios-xaga`
- MT6895 UEFI: `CrackerCat/EDK2-Xiaomi-MediaTek-MT6895`
- MT6895 hardware values: `MT6895-Mainline/linux`

## Safety

All work so far is on PC/GitHub only. No device writes. Device has U盘 full firmware backup.
