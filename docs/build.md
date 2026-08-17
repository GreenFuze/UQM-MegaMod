# Building UQM AI Edition (Windows)

Reproducible build instructions for the AI Edition fork of UQM-MegaMod.

Baseline: MegaMod tag **0.8.5**, branch `ai-edition`.

---

## 1. Prerequisites

| Tool | Version verified | Notes |
|---|---|---|
| Visual Studio 2022 Community | 17.14 (MSVC 14.44, toolset v143) | Only the C++ desktop workload is needed |
| Git | 2.53 | |
| PowerShell | 7+ (`pwsh`) | For the content packaging script |

MSYS2/MinGW is **not** required for the Windows build. Upstream CI uses MSYS2 MINGW32,
but the MSVC solution is self-contained because `dev-lib\` ships prebuilt 32-bit
dependencies (SDL2, libpng, ogg, vorbis, zlib, OpenAL).

### Architecture: 32-bit only

The game builds as **x86-32**. This is deliberate — see [x64 status](#x64-status).
It does not constrain the AI subsystem, which runs as a separate 64-bit sidecar process.

---

## 2. Repository layout

```
UQMAI\
├── uqm-megamod\          # this fork (origin), upstream = JHGuitarFreak/UQM-MegaMod
├── uqm-official\         # SourceForge sc2/uqm - read-only canonical reference
└── uqm-megamod-content\  # content source assets
```

```bash
git clone git@github.com:GreenFuze/UQM-MegaMod.git uqm-megamod
git clone https://git.code.sf.net/p/sc2/uqm uqm-official
```

---

## 3. Content — read this before cloning

> [!IMPORTANT]
> Clone the content repository with `core.autocrlf=false`.

Git's Windows default (`core.autocrlf=true`) rewrites LF to CRLF on checkout. The content
repo contains roughly **826 text asset files** — `.ani` animation descriptors and
`uqm.rmp` — whose parser cannot tolerate the stray carriage return.

The failure mode is nasty because it is not a crash: the game launches, reaches the menu,
and renders **corrupted fonts, broken menus, and wrong graphics**. It looks like a
rendering or version-mismatch bug, not a checkout bug.

Clone with LF preserved, and **do not use `--depth 1`** — a shallow clone hides the release
tags you need in the next step:

```bash
git -c core.autocrlf=false clone https://github.com/JHGuitarFreak/UQM-MegaMod-Content.git uqm-megamod-content
```

Repairing an existing bad checkout:

```bash
git -C uqm-megamod-content config core.autocrlf false
git -C uqm-megamod-content rm --cached -r .
git -C uqm-megamod-content reset --hard
```

**Verify before building** — this file must be exactly 30 bytes (31 means CRLF corruption):

```bash
ls -l uqm-megamod-content/base/planets/alkali-med.ani
```

### Pin the content to the matching release tag

> [!IMPORTANT]
> Check out the content tag matching the game version. For baseline 0.8.5:

```bash
git -C uqm-megamod-content checkout 0.8.5
```

The content repository carries per-release tags (`0.8.1` … `0.8.5`), and its `master`
branch runs **ahead** of the shipped release. Building from `master` is not merely
"slightly newer" — `base/gamestrings.txt` is a **position-indexed string table**, and the
executable looks strings up by index. A newer table shifts every index, so the game shows
the *wrong strings* instead of failing:

- main menu loses entries (`Setup`, `Exit` vanish)
- difficulty-description text leaks onto the main menu, overflowing and cropping
- the mode indicator row reads `Extended | Nomad` instead of the music credit

Nothing logs an error, because from the engine's point of view every lookup succeeded.
`tools/build-content-package.ps1` refuses to build unless the checkout is at the expected
tag; override with `-AllowTagMismatch` only if you know why.

### Building the content package

A `.uqm` file is a plain zip. `mountBaseZip()` in [`src/options.c`](../src/options.c)
locates it inside `content/packages/` by matching **CRC32 of the filename** against
`BASE_CONTENT_NAME` (`mm-<version>-content.uqm`, i.e. `mm-0.8.5-content.uqm`), then mounts
it at `/`. The archive root must therefore contain `base/` alongside `menu.key`,
`uqm.key` and `uqm.rmp`.

```bash
pwsh tools/build-content-package.ps1
```

This produces `content/packages/mm-0.8.5-content.uqm` — 25.3 MB, 12,471 entries, which
matches the official package exactly (0 missing, 0 extra, 0 size mismatches).

Two non-obvious requirements the script handles, both of which produce a package that
mounts without error but renders incorrectly:

**Explicit directory entries.** `uio`'s zip reader builds its directory tree from
zero-length entries whose names end in `/` (see `src/libs/uio/zip/zip.c`). Most naive zip
writers omit these. Ordinary files are opened by exact path and still work, so art loads
fine — but a UQM **font is a directory** (`base/fonts/*.fon/` holding per-glyph PNGs), and
fonts need directory *enumeration*. Omit the entries and you get correct artwork with
broken text.

**MS-DOS attribute bits.** The reader classifies an entry as a directory from the
`FILE_ATTRIBUTE_DIRECTORY` bit in the zip's external-attributes field, not from the
trailing slash. The official package uses `0x30` for directories and `0x20` for files.
.NET's `ZipArchive` leaves this at `0x00`, which makes the reader treat `base/` as a file
with an illegal name:

```
Warning: 'base/' is not a valid file name - skipped.
```

**Empty directories.** `base/ui/meleeatlas/` is empty in the shipped package. Git cannot
represent an empty directory, so it is absent from the checkout and the script re-adds it
explicitly.

### Addons

Addons live in `content/addons/`. Packaged `.uqm` addons from an official MegaMod install
work directly — the HD graphics (`mm-0.8.5-hd-content.uqm`) and 3DO voice
(`mm-0.8.4-3dovoice.uqm`) sets are the notable ones. The 3DO voice pack matters to AI
Edition beyond cosmetics: it is the reference audio for character voice work, and its
`.ts` files index every voiced line by symbolic dialogue key.

---

## 4. Build

```bash
MSBuild.exe build/msvs2019/UrQuanMastersMegaMod.sln /p:Configuration=Release /p:Platform=Win32 /m
```

MSBuild lives at
`C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe`.

A clean build takes about 20 seconds and produces `UrQuanMasters.exe` in the repository
root. Debug configuration produces `UrQuanMastersDebug.exe`.

Copy the runtime DLLs next to the executable (once, or whenever `dev-lib` changes):

```bash
cp dev-lib/lib/*.dll .
```

Then run `UrQuanMasters.exe` from the repository root.

---

## 5. Two build systems

MegaMod maintains **both** an MSVC project and a CMake build:

- `build/msvs2019/UrQuanMasters.vcxproj` — Windows / MSVC
- `CMakeLists.txt` — Linux, macOS, MinGW (what upstream CI uses)

New source files must be registered in **both**, or the local Windows build succeeds while
Linux and CI break. This applies to every file AI Edition adds.

---

## 6. x64 status

x64 is **parked**, not abandoned. Two genuine defects were found and diagnosed:

1. **Build failure.** `src/libs/graphics/sdl/2xscalers_mmx.c` contains inline MMX assembly
   that emits `(%rcx,%ebx,4)` — a 64-bit base with a 32-bit index, invalid on x86-64.
   This is an LP64/LLP64 split: on Linux `long` is 64-bit so the index lands in `%rbx` and
   assembles fine, which is why the x86_64 Linux CI passes. On Windows `long` stays 32-bit.
   Workaround: `-DUQM_PLATFORM_ACCEL=OFF`.

2. **Heap corruption.** With acceleration disabled the x64 binary builds, runs and renders
   correctly, but corrupts the heap — crashing with `0xc0000005` inside `ntdll.dll` at
   teardown. Not diagnosed further.

Reproducing the x64 attempt (MSYS2 MINGW64):

```bash
pacman -S --needed base-devel mingw-w64-x86_64-toolchain mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja mingw-w64-x86_64-SDL2 mingw-w64-x86_64-libpng mingw-w64-x86_64-libogg mingw-w64-x86_64-libvorbis mingw-w64-x86_64-zlib
cmake . -G Ninja -DCMAKE_BUILD_TYPE=Release -DUQM_PLATFORM_ACCEL=OFF && ninja
./msys2-depend.sh   # copies MINGW64 runtime DLLs next to the binary
```

---

## 7. Verifying a good build

The main menu must show **five** entries — `New Game`, `Load Game`, `Super Melee!`,
`Setup`, `Exit` — with a music credit bottom-left and `v0.8.5 HD MegaMod` bottom-right.

Symptom-to-cause for the content failures, all of which still boot:

| Symptom | Cause |
|---|---|
| Mangled fonts, broken menus, wrong graphics | CRLF-corrupted checkout ([section 3](#3--content--read-this-before-cloning)) |
| Correct artwork, wrong/oversized text | Missing zip directory entries or `0x00` attributes |
| Menu missing `Setup`/`Exit`, description text leaking and cropped | Content not pinned to tag `0.8.5` |

Because none of these log an error, the reliable check is to **diff the game's stderr
against a known-good run**. Capture with `Start-Process -RedirectStandardError`, then
`Compare-Object`. A good run emits ~275 lines; the only expected differences are the
randomly selected `mainmenu<N>.ogg` track and an occasional renderer `blocking on 'DCQ'`
timing line.

To distinguish a real crash from a normal exit, check the Windows event log rather than
trusting the process exit code:

```bash
pwsh -c "Get-WinEvent -FilterHashtable @{LogName='Application';ProviderName='Application Error';StartTime=(Get-Date).AddMinutes(-10)} | Where-Object { $_.Message -match 'UrQuanMasters' }"
```
