#!/usr/bin/env python3
"""
Ultrawide (21:9 / 32:9) patch for
The Lord of the Rings: War in the North - Legacy Edition (Aspyr remaster).

WHY THE GAME HAS NO ULTRAWIDE SUPPORT
-------------------------------------
witn.exe enumerates the display's video modes and only keeps a mode if its
width/height falls within +/-0.0001 of one of FOUR hardcoded aspect ratios:

    .rdata @ file 0x00A46C50 (VA 0x140A48050)
        [0] 1.777778   16:9
        [1] 1.600000   16:10
        [2] 1.333333   4:3
        [3] 1.250000   5:4

Any mode that matches none of them is silently dropped, so 2560x1080
(2.370370), 3440x1440 (2.388889), 3840x1600 (2.400000) and 32:9 modes never
reach the "Change Resolution" menu. The four buckets are also a fixed-size
array in the caller's stack frame, so the table cannot simply be made longer -
entries have to be replaced.

WHAT THIS PATCH DOES
--------------------
  aspect_table  Replaces the two legacy entries (4:3 and 5:4) with ultrawide
                ratios. 16:9 and 16:10 are left untouched.
  gui_canvas    Rewrites the tail of the virtual-canvas function
                (witn.exe+0x208940) so the UI canvas is anchored to its height
                once the screen is wider than 16:9, instead of always being
                1280 design units wide. Stock, a 21:9 screen gets a 1280x549
                canvas and the HUD rows below y=549 - party health bars, item
                hotbar - fall off the bottom of the screen. Patched, it gets
                1680x720: full design height, extra aspect spent on width.
                Every aspect at or below 16:9 is bit-identical to stock.

  gui_pdata     Nudges that function's .pdata EndAddress, because the
                replacement is 8 bytes longer than the code it replaces and
                runs into the int3 padding behind it.

  hud_proj      The in-game HUD does not use that function - witn.exe+0x208a70
  hud_cave      builds the UI's orthographic projection with the same canvas
  text_vsize    math inlined, so it needs the identical fix. The replacement
                does not fit in place, so the site jumps to the tail padding of
                .text and back, and .text's VirtualSize is raised by 0x151
                bytes so the loader maps that padding.

                Note: the trampoline body has no .pdata entry, so it is treated
                as a leaf frame if anything ever unwinds through it. It makes
                no calls and cannot fault, so only an asynchronous exception
                landing on those 112 bytes could notice.

  tolerance     Repoints the mode-matching epsilon in the enumeration loop
                (witn.exe+0x7A2A78) from an existing 0.0001 constant to an
                existing 0.04 constant already present in .rdata. This lets
                one table slot cover a whole ultrawide family instead of a
                single exact resolution. Nothing is written to .rdata; only
                the instruction's RIP displacement changes.

                The same epsilon is reused a few instructions later by the
                duplicate-mode filter, which compares integer pixel counts -
                two distinct modes differ by at least 1.0 there, so widening
                it to 0.04 cannot merge them.

  Match windows after patching (no two overlap, all gaps are wide):
      16:9   1.777778 +/- 0.04 -> 1.73778 - 1.81778
      16:10  1.600000 +/- 0.04 -> 1.56000 - 1.64000
      21:9   2.366700 +/- 0.04 -> 2.32670 - 2.40670
      32:9   3.555556 +/- 0.04 -> 3.51556 - 3.59556

Trade-off: 4:3 and 5:4 modes no longer show up in the resolution menu.

USAGE
-----
    python witn_ultrawide.py status
    python witn_ultrawide.py apply
    python witn_ultrawide.py revert
    python witn_ultrawide.py set-res 2560 1080     (force it in GameSettings.dat)

Steam's "Verify integrity of game files" will restore the stock witn.exe.
Re-run `apply` afterwards.
"""

import argparse
import os
import shutil
import struct
import sys
import zlib

GAME_DIR = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(GAME_DIR, "witn.exe")
BACKUP = os.path.join(GAME_DIR, "witn.exe.orig")

SETTINGS_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Aspyr", "War in the North"
)
GAME_SETTINGS = os.path.join(SETTINGS_DIR, "GameSettings.dat")

# --- patch definitions -------------------------------------------------------

ASPECT_TABLE_OFF = 0x00A46C50  # VA 0x140A48050, four float32 entries

STOCK_RATIOS = (16 / 9, 1.6, 4 / 3, 1.25)
# "21:9" is not one ratio. The family spans 2.333333 (1680x720, the only mode
# that is literally 21/9) up to 2.400000 (3840x1600), with 2560x1080 and
# 3440x1440 in between. Slot 2 sits at the centre of that span so a single
# entry plus the 0.04 tolerance below covers 2.32670 - 2.40670, i.e. all of:
#     1680x720   2.333333      3440x1440  2.388889
#     2560x1080  2.370370      3840x1600  2.400000
#     5120x2160  2.370370
PATCHED_RATIOS = (16 / 9, 1.6, 2.3667, 32 / 9)

EPS_INSN_OFF = 0x007A2A78  # movss xmm6, [rip+disp32]   (8 bytes)
EPS_DISP_OFF = EPS_INSN_OFF + 4  # the disp32 field itself
EPS_INSN_NEXT_VA = 0x1407A3680  # VA of the following instruction
EPS_TIGHT_VA = 0x1409F2F20  # existing 0.0001f constant  (stock target)
EPS_WIDE_VA = 0x1409FE364  # existing 0.04f   constant  (patched target)

EPS_INSN_PREFIX = bytes.fromhex("f30f1035")  # movss xmm6, [rip+...]

# --- GUI canvas ---------------------------------------------------------------
#
# witn.exe+0x208940 (VA 0x140209540) builds the virtual canvas that every UI
# element is laid out in.  Stock:
#
#       aspect  = backbufferW / backbufferH
#       scale   = 0.9 if aspect is nearer 4:3 than 16:9 else 1.0
#       canvasW = scale * 1280
#       canvasH = canvasW / aspect
#       return    canvasH / 720          (all three callers ignore it)
#
# The canvas always matches the screen aspect, so the HUD is never distorted.
# But its height in design units shrinks as the screen gets wider:
#
#       16:9  -> 1280x720       4:3  -> 1152x864
#       16:10 -> 1280x800       21:9 -> 1280x549   <-- only 549 design rows
#
# Anything the HUD places below design y=549 - the party health bars down the
# left, the item hotbar along the bottom - falls off the screen.  That is the
# clipping seen at 1680x720.
#
# Patched: anchor to the height instead, but only once the screen is wider than
# 16:9, so every aspect the stock game already supported is left alone:
#
#       a       = min(aspect, 16/9)
#       canvasH = scale * 1280 / a
#       canvasW = canvasH * aspect
#
# For aspect <= 16/9 that is algebraically the stock formula, and in float32 it
# is bit-identical (16:9 1280x720, 16:10 1280x800, 4:3 1152x864,
# 5:4 1152x921.6).  At 21:9 it gives 1680x720 instead of 1280x549: the full
# design height back, the extra aspect spent on width, HUD anchored to the real
# edges of the screen.
#
# The 62-byte replacement overruns the function tail by 8 bytes into the int3
# padding that follows it, so the .pdata EndAddress is nudged to match.

GUI_CANVAS_OFF = 0x00208A28  # VA 0x140209628, tail of the canvas function
GUI_CANVAS_STOCK = bytes.fromhex(
    "f30f5edd"          # divss  xmm3, xmm5          ; 1/aspect
    "0f28d4"            # movaps xmm2, xmm4
    "f30f5915b9c97e00"  # mulss  xmm2, [1280.0]      ; canvasW = scale*1280
    "0f28cb"            # movaps xmm1, xmm3
    "f30f591d6a4f7f00"  # mulss  xmm3, [16/9]
    "f30f59ca"          # mulss  xmm1, xmm2          ; canvasH = canvasW/aspect
    "f30f59dc"          # mulss  xmm3, xmm4
    "f30f1111"          # movss  [rcx],   xmm2
    "f30f114904"        # movss  [rcx+4], xmm1
    "0f28c3"            # movaps xmm0, xmm3
    "4881c408010000"    # add    rsp, 0x108
    "c3"                # ret
    "cccccccccccccccc"  # int3 padding
)
GUI_CANVAS_PATCHED = bytes.fromhex(
    "0f28c5"            # movaps xmm0, xmm5          ; xmm0 = real aspect
    "f30f5d2d794f7f00"  # minss  xmm5, [16/9]        ; xmm5 = min(aspect, 16/9)
    "f30f5edd"          # divss  xmm3, xmm5          ; xmm3 = 1/clamped
    "f30f5925b1c97e00"  # mulss  xmm4, [1280.0]      ; scale*1280
    "f30f59e3"          # mulss  xmm4, xmm3          ; canvasH
    "0f28cc"            # movaps xmm1, xmm4
    "f30f59c8"          # mulss  xmm1, xmm0          ; canvasW = canvasH*aspect
    "f30f1109"          # movss  [rcx],   xmm1
    "f30f116104"        # movss  [rcx+4], xmm4
    "0f28c4"            # movaps xmm0, xmm4
    "f30f5e055ea27f00"  # divss  xmm0, [720.0]       ; return canvasH/720
    "4881c408010000"    # add    rsp, 0x108
    "c3"                # ret
)
assert len(GUI_CANVAS_STOCK) == len(GUI_CANVAS_PATCHED) == 62

GUI_CANVAS_ANCHOR = bytes.fromhex("f30f5edd")  # divss xmm3, xmm5 (stock head)

GUI_PDATA_OFF = 0x00BAFD30  # RUNTIME_FUNCTION.EndAddress for the canvas fn
GUI_PDATA_STOCK = struct.pack("<I", 0x0020965E)
GUI_PDATA_PATCHED = struct.pack("<I", 0x00209666)

# --- HUD projection ----------------------------------------------------------
#
# The HUD does not go through the canvas function above: witn.exe+0x208a70
# (VA 0x140209670) builds the UI orthographic projection and has the very same
# canvas math *inlined*, which is why patching 0x140209540 alone fixed the menus
# but left the in-game HUD clipped.
#
# What that function does, per viewport:
#       canvasW = scale * 1280                 (inlined, width-anchored)
#       canvasH = canvasW / aspect
#       vpW     = canvasW * (x1 - x0)          x0..x1, y0..y1 are the viewport
#       vpH     = canvasH * (y1 - y0)          fractions, 0..1 in single player
#       ortho half-extents = (vpW/vpH, canvasH/720)
#
# HUD widgets live in a *fixed* 1280x720 design space (witn.exe+0x2b83e0
# returns exactly viewportFraction * 1280 x 720), and the projection centres
# that box inside the canvas box.  So the visible slice of the design space is
# canvasW x canvasH, centred:
#
#       16:9  -> 1280x720 of 1280x720   nothing lost
#       4:3   -> 1152x864 of 1280x720   64 units lost off each side
#       21:9  -> 1280x549 of 1280x720   85 units lost off the top AND bottom
#
# That is the party health bars off the bottom and the compass off the top.
#
# Patched, with the same min(aspect, 16/9) clamp as the canvas function, 21:9
# gives a 1680x720 slice: all 720 design rows visible, and the 1280-wide design
# box centred with 200 units of empty screen either side.  The HUD ends up
# pillarboxed rather than clipped, which is the usual outcome for a game whose
# UI design space is a fixed size.
#
# The replacement is 107 bytes over a 93-byte window, so the window gets a jmp
# into the 337 bytes of zero padding at the tail of .text, and a jmp back.
# .text's VirtualSize stops one byte short of that padding, so it is raised to
# its SizeOfRawData - still well inside the 0x1000-aligned span the section
# already occupies, and nowhere near .rdata at RVA 0x9e6000.

HUD_HOOK_OFF = 0x00208BC3  # VA 0x1402097C3, 93 bytes, no branch targets inside
HUD_HOOK_STOCK = bytes.fromhex("410f28f8f3440f11442430f30f5efb0f28c40f57c9f30f590510c87e000f28f7f30f59f5f30f59f8f3440f59c8f3410f104104f3410f5c01410f28d1f30f114c2428f3410f5cd0f30f114c2420f30f59f4f30f59f80f28dff3410f5cd8")
HUD_HOOK_PATCHED = bytes.fromhex("e9e8be7d00cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc")

HUD_CAVE_OFF = 0x009E4AB0  # VA 0x1409E56B0, inside the .text tail padding
HUD_CAVE_STOCK = bytes(112)
HUD_CAVE_PATCHED = bytes.fromhex("0f28d3f30f5ddd410f28f8f3440f11442430f30f5efb0f28c40f57c9f30f59051c090100f30f59f80f28c7f30f59c20f28f7f30f5e3502090100f3440f59c8f3410f104104f3410f5c01410f28d1f30f114c2428f3410f5cd0f30f114c2420f30f59f80f28dff3410f5cd8e9004182ff")

TEXT_VSIZE_OFF = 0x00000268  # .text section header, VirtualSize
TEXT_VSIZE_STOCK = struct.pack("<I", 0x009E46AF)
TEXT_VSIZE_PATCHED = struct.pack("<I", 0x009E4800)

# --- character-select camera --------------------------------------------------
#
# The 3D character-select preview is stretched horizontally at 21:9 while
# gameplay is not, because it uses a different camera.  witn.exe+0x281264
# stores a hardcoded 16/9 into a camera-parameter global, and the one routine
# that reads it (witn.exe+0x298600) derives the horizontal field of view from
# the vertical one with it:
#
#       halfFovY = fovY * 0.5                  fovY is a fixed 40 degrees
#       halfFovX = halfFovY * thatGlobal       <-- always 16/9
#
# So the preview renders a 16:9 frustum into whatever the viewport actually is.
# At 1720x720 that is a 2.3889/1.7778 = 1.344x horizontal stretch, which is
# what the earlier 2-D scale search measured on that screen (sx 1.337, sy 1.000).
#
# Patched, the store becomes a call into the .text tail padding that reads the
# real backbuffer size from the same display struct the UI canvas uses, so the
# preview picks up the same aspect as everything else and widens horizontally
# instead of stretching.  The helper preserves rax/xmm0/xmm1 and touches
# nothing else, so the call is transparent to the surrounding function, and it
# falls back to 16/9 if the display struct is not populated yet.

CAM_HOOK_OFF = 0x00280664  # VA 0x140281264, mov dword [rip+...], 0x3FE38E39
CAM_HOOK_STOCK = bytes.fromhex("c7058ee88e00398ee33f")
CAM_HOOK_PATCHED = bytes.fromhex("e8b74476000f1f440000")

CAM_CAVE_OFF = 0x009E4B20  # VA 0x1409E5720, just past the HUD trampoline
CAM_CAVE_STOCK = bytes(92)
CAM_CAVE_PATCHED = bytes.fromhex("4883ec18f30f110424f30f114c24044889442408488d05955a1800837848007e1683784c007e10f30f2a4048f30f2a484cf30f5ec1eb08f30f10054d8e0100f30f110595a31800488b442408f30f104c2404f30f1004244883c418c3")


# --- gameplay field of view: Vert- -> Hor+ ------------------------------------
#
# Stock, a wider screen does not show you more.  Two gameplay screenshots of
# the same spot, 3440x1440 and 2560x1440, differ by a uniform 1.3437x zoom with
# no anisotropy - and 3440/2560 is 1.3438.  Same horizontal content, top and
# bottom cropped off.  That is Vert-, and it is the wrong way round for an
# ultrawide monitor.
#
# tanf is witn.exe+0x7dde90.  Only ten call sites in the binary, two of which
# are perspective builders:
#
#       witn.exe+0x7b18e0   plain
#       witn.exe+0x7b1e00   same, then concatenates a second matrix
#
# Both are (out=rcx, zNear=xmm1, zFar=xmm2, fov=xmm3, aspect=stack5, k=stack6):
#
#       m00 = k / tan(fov / 2)          fov here is the HORIZONTAL fov
#       m11 = m00 * aspect
#
# m11/m00 is the aspect by definition, which is how the stack arguments were
# identified.  Anchoring on the horizontal is the console default and it is
# what shrinks the vertical fov as the screen widens.
#
# Fix is to scale m00 by (16/9)/aspect.  m11 comes off m00 further down, so it
# scales too and the aspect cancels:
#
#       m00' = m00 * (16/9) / aspect
#       m11' = m00' * aspect = m00 * 16/9        no aspect term left
#
# Vertical fov pinned to its 16:9 value, horizontal opens up instead: Hor+.
#
# Each hook eats "divss <reg>, xmm0" plus the "mov qword [rsp+0x2c], 0" behind
# it - 13 bytes, enough for a jmp rel32 - and re-runs both in the cave.  xmm0
# holds tan(fov/2) and is dead once the divide retires (both functions
# overwrite it before reading it again), so the cave borrows it for the aspect.
# 16/9 comes from the constant already in .rdata at VA 0x1409fe5ac.
#
# Guarded on aspect > 16/9, so 16:9 and narrower are untouched, and so are the
# square and 16:9 projections used by shadow and reflection passes.  comiss
# leaves CF=ZF=PF set on a NaN and the jbe is taken, so a garbage aspect falls
# back to stock rather than to a garbage frustum.
#
# Known side effect: the shadow pass builds its frustum from the unscaled fov
# (witn.exe+0x2922b0 does its own tan), so shadows stop short of the far left
# and right of the screen.  Widening it would spread the same shadow map over
# more ground and soften shadows everywhere, so it is left alone.

FOV_A_HOOK_OFF = 0x007B0D4D  # VA 0x1407b194d, in the plain perspective builder
FOV_A_HOOK_STOCK = bytes.fromhex("f30f5ec848c744242c00000000")
FOV_A_HOOK_PATCHED = bytes.fromhex("e92e3e23009090909090909090")

FOV_A_CAVE_OFF = 0x009E4B80  # VA 0x1409e5780, just past the camera helper
FOV_A_CAVE_STOCK = bytes(48)
FOV_A_CAVE_PATCHED = bytes.fromhex("f30f5ec8f30f108424e00000000f2f05188e0100760cf30f590d0e8e0100f30f5ec848c744242c00000000e9aac1dcff")

FOV_B_HOOK_OFF = 0x007B129C  # VA 0x1407b1e9c, the concatenating variant
FOV_B_HOOK_STOCK = bytes.fromhex("f30f5ee048c744242c00000000")
FOV_B_HOOK_PATCHED = bytes.fromhex("e90f3923009090909090909090")

FOV_B_CAVE_OFF = 0x009E4BB0  # VA 0x1409e57b0
FOV_B_CAVE_STOCK = bytes(48)
FOV_B_CAVE_PATCHED = bytes.fromhex("f30f5ee0f30f108424500100000f2f05e88d0100760cf30f5925de8d0100f30f5ee048c744242c00000000e9c9c6dcff")


def _f4(values):
    return b"".join(struct.pack("<f", v) for v in values)


def _disp(target_va):
    return struct.pack("<i", target_va - EPS_INSN_NEXT_VA)


PATCHES = [
    {
        "name": "aspect_table",
        "offset": ASPECT_TABLE_OFF,
        "stock": _f4(STOCK_RATIOS),
        "patched": _f4(PATCHED_RATIOS),
        "desc": "aspect-ratio whitelist: 4:3 and 5:4 -> 21:9 and 32:9",
    },
    {
        "name": "tolerance",
        "offset": EPS_DISP_OFF,
        "stock": _disp(EPS_TIGHT_VA),
        "patched": _disp(EPS_WIDE_VA),
        "desc": "mode-match tolerance: 0.0001 -> 0.04",
    },
    {
        "name": "gui_canvas",
        "offset": GUI_CANVAS_OFF,
        "stock": GUI_CANVAS_STOCK,
        "patched": GUI_CANVAS_PATCHED,
        "desc": "UI canvas: width-anchored -> height-anchored above 16:9",
    },
    {
        "name": "gui_pdata",
        "offset": GUI_PDATA_OFF,
        "stock": GUI_PDATA_STOCK,
        "patched": GUI_PDATA_PATCHED,
        "desc": "unwind EndAddress for the rewritten canvas function",
    },
    {
        "name": "hud_proj",
        "offset": HUD_HOOK_OFF,
        "stock": HUD_HOOK_STOCK,
        "patched": HUD_HOOK_PATCHED,
        "desc": "HUD ortho projection: jmp to the rewritten canvas math",
    },
    {
        "name": "hud_cave",
        "offset": HUD_CAVE_OFF,
        "stock": HUD_CAVE_STOCK,
        "patched": HUD_CAVE_PATCHED,
        "desc": "the rewritten math, in the .text tail padding",
    },
    {
        "name": "text_vsize",
        "offset": TEXT_VSIZE_OFF,
        "stock": TEXT_VSIZE_STOCK,
        "patched": TEXT_VSIZE_PATCHED,
        "desc": ".text VirtualSize raised so the padding is mapped",
    },
    {
        "name": "cam_aspect",
        "offset": CAM_HOOK_OFF,
        "stock": CAM_HOOK_STOCK,
        "patched": CAM_HOOK_PATCHED,
        "desc": "character-select camera: hardcoded 16/9 -> real aspect",
    },
    {
        "name": "cam_cave",
        "offset": CAM_CAVE_OFF,
        "stock": CAM_CAVE_STOCK,
        "patched": CAM_CAVE_PATCHED,
        "desc": "the aspect helper, in the .text tail padding",
    },
    {
        "name": "fov_projA",
        "offset": FOV_A_HOOK_OFF,
        "stock": FOV_A_HOOK_STOCK,
        "patched": FOV_A_HOOK_PATCHED,
        "desc": "gameplay FOV: jmp out of the plain perspective builder",
    },
    {
        "name": "fov_caveA",
        "offset": FOV_A_CAVE_OFF,
        "stock": FOV_A_CAVE_STOCK,
        "patched": FOV_A_CAVE_PATCHED,
        "desc": "Vert- -> Hor+ correction, in the .text tail padding",
    },
    {
        "name": "fov_projB",
        "offset": FOV_B_HOOK_OFF,
        "stock": FOV_B_HOOK_STOCK,
        "patched": FOV_B_HOOK_PATCHED,
        "desc": "gameplay FOV: jmp out of the concatenating builder",
    },
    {
        "name": "fov_caveB",
        "offset": FOV_B_CAVE_OFF,
        "stock": FOV_B_CAVE_STOCK,
        "patched": FOV_B_CAVE_PATCHED,
        "desc": "the same correction for that builder",
    },
]


# --- helpers -----------------------------------------------------------------


def read_exe():
    if not os.path.isfile(EXE):
        sys.exit("witn.exe not found next to this script (%s)" % GAME_DIR)
    with open(EXE, "rb") as fh:
        return bytearray(fh.read())


def check_anchor(buf):
    """Guard against a game update having moved things around."""
    cam = bytes(buf[CAM_HOOK_OFF:CAM_HOOK_OFF + 1])
    if cam not in (CAM_HOOK_STOCK[:1], CAM_HOOK_PATCHED[:1]):
        sys.exit("Unexpected bytes at 0x%08X (found %s) - witn.exe is not the "
                 "build this patch was written for; aborting."
                 % (CAM_HOOK_OFF, cam.hex()))
    hud = bytes(buf[HUD_HOOK_OFF:HUD_HOOK_OFF + 4])
    if hud not in (HUD_HOOK_STOCK[:4], HUD_HOOK_PATCHED[:4]):
        sys.exit("Unexpected bytes at 0x%08X (found %s) - witn.exe is not the "
                 "build this patch was written for; aborting."
                 % (HUD_HOOK_OFF, hud.hex()))
    head = bytes(buf[GUI_CANVAS_OFF:GUI_CANVAS_OFF + 4])
    if head not in (GUI_CANVAS_ANCHOR, GUI_CANVAS_PATCHED[:4]):
        sys.exit("Unexpected bytes at 0x%08X (found %s) - witn.exe is not the "
                 "build this patch was written for; aborting."
                 % (GUI_CANVAS_OFF, head.hex()))
    got = bytes(buf[EPS_INSN_OFF:EPS_INSN_OFF + 4])
    if got != EPS_INSN_PREFIX:
        sys.exit(
            "Unexpected bytes at 0x%08X (found %s, expected %s).\n"
            "witn.exe does not look like the build this patch was written for; "
            "aborting so nothing is corrupted." % (EPS_INSN_OFF, got.hex(), EPS_INSN_PREFIX.hex())
        )


def state_of(buf, patch):
    off, n = patch["offset"], len(patch["stock"])
    cur = bytes(buf[off:off + n])
    if cur == patch["patched"]:
        return "patched"
    if cur == patch["stock"]:
        return "stock"
    return "unknown"


def show_table(buf):
    vals = struct.unpack_from("<4f", buf, ASPECT_TABLE_OFF)
    print("  aspect table @0x%08X: %s" % (ASPECT_TABLE_OFF, ", ".join("%.6f" % v for v in vals)))
    disp = struct.unpack_from("<i", buf, EPS_DISP_OFF)[0]
    target = EPS_INSN_NEXT_VA + disp
    label = {EPS_TIGHT_VA: "0.0001", EPS_WIDE_VA: "0.04"}.get(target, "?")
    print("  match tolerance          : %s  (VA 0x%X)" % (label, target))

    head = bytes(buf[GUI_CANVAS_OFF:GUI_CANVAS_OFF + 4])
    heights = head == GUI_CANVAS_PATCHED[:4]
    print("  UI canvas anchor         : %s" % ("height" if heights else "width"))
    for name, asp in (("16:9 ", 16 / 9), ("16:10", 1.6), ("21:9 ", 2560 / 1080)):
        a = min(asp, 16 / 9) if heights else asp
        h = 1280.0 / a
        print("      %s -> %6.1f x %5.1f design units" % (name, h * asp, h))


# --- commands ----------------------------------------------------------------


def cmd_status(_args):
    buf = read_exe()
    check_anchor(buf)
    print("witn.exe: %s (%d bytes)" % (EXE, len(buf)))
    print("backup  : %s" % (BACKUP if os.path.isfile(BACKUP) else "(none yet)"))
    for p in PATCHES:
        print("  [%-7s] %-14s  %s" % (state_of(buf, p), p["name"], p["desc"]))
    show_table(buf)
    return 0


def cmd_apply(_args):
    buf = read_exe()
    check_anchor(buf)

    for p in PATCHES:
        st = state_of(buf, p)
        if st == "unknown":
            sys.exit(
                "Patch '%s' cannot be applied: bytes at 0x%08X are neither stock "
                "nor patched. Restore witn.exe (Steam -> Verify integrity) first."
                % (p["name"], p["offset"])
            )

    if not os.path.isfile(BACKUP):
        shutil.copy2(EXE, BACKUP)
        print("backed up stock exe -> %s" % os.path.basename(BACKUP))
    else:
        print("backup already present -> %s" % os.path.basename(BACKUP))

    changed = 0
    for p in PATCHES:
        if state_of(buf, p) == "patched":
            print("  already patched: %s" % p["name"])
            continue
        off = p["offset"]
        buf[off:off + len(p["patched"])] = p["patched"]
        print("  patched %-14s @0x%08X  %s -> %s"
              % (p["name"], off, p["stock"].hex(), p["patched"].hex()))
        changed += 1

    if changed:
        tmp = EXE + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(buf)
        os.replace(tmp, EXE)
        print("wrote witn.exe")
    else:
        print("nothing to do")

    show_table(buf)
    return 0


def cmd_revert(_args):
    if os.path.isfile(BACKUP):
        shutil.copy2(BACKUP, EXE)
        print("restored stock witn.exe from %s" % os.path.basename(BACKUP))
        return 0

    buf = read_exe()
    check_anchor(buf)
    for p in PATCHES:
        if state_of(buf, p) == "unknown":
            sys.exit("No backup, and bytes at 0x%08X are unrecognised - "
                     "use Steam -> Verify integrity of game files." % p["offset"])
    for p in PATCHES:
        buf[p["offset"]:p["offset"] + len(p["stock"])] = p["stock"]
    tmp = EXE + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf)
    os.replace(tmp, EXE)
    print("reverted patches in place (no backup file existed)")
    return 0


def cmd_set_res(args):
    """Force a resolution into GameSettings.dat.

    Layout (little-endian):
        0x00  uint32  crc32 of everything from 0x04 to EOF
        0x04  uint32  version (9)
        0x2C  uint32  width
        0x30  uint32  height
    """
    if not os.path.isfile(GAME_SETTINGS):
        sys.exit("GameSettings.dat not found at %s\n"
                 "Launch the game once so it gets created." % GAME_SETTINGS)

    with open(GAME_SETTINGS, "rb") as fh:
        buf = bytearray(fh.read())

    ver = struct.unpack_from("<I", buf, 0x04)[0]
    if ver != 9:
        sys.exit("Unexpected GameSettings.dat version %d - not touching it." % ver)
    if struct.unpack_from("<I", buf, 0x00)[0] != (zlib.crc32(buf[4:]) & 0xFFFFFFFF):
        sys.exit("GameSettings.dat checksum does not validate - not touching it.")

    old = struct.unpack_from("<II", buf, 0x2C)
    bak = GAME_SETTINGS + ".bak"
    if not os.path.isfile(bak):
        shutil.copy2(GAME_SETTINGS, bak)
        print("backed up -> %s" % os.path.basename(bak))

    struct.pack_into("<II", buf, 0x2C, args.width, args.height)
    struct.pack_into("<I", buf, 0x00, zlib.crc32(buf[4:]) & 0xFFFFFFFF)

    with open(GAME_SETTINGS, "wb") as fh:
        fh.write(buf)
    print("resolution %dx%d -> %dx%d (crc fixed up)"
          % (old[0], old[1], args.width, args.height))
    print("note: the game snaps this to the closest mode the display actually "
          "reports, within the matching aspect bucket.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show which patches are applied").set_defaults(fn=cmd_status)
    sub.add_parser("apply", help="apply the ultrawide patches").set_defaults(fn=cmd_apply)
    sub.add_parser("revert", help="restore the stock executable").set_defaults(fn=cmd_revert)

    sr = sub.add_parser("set-res", help="force a resolution into GameSettings.dat")
    sr.add_argument("width", type=int)
    sr.add_argument("height", type=int)
    sr.set_defaults(fn=cmd_set_res)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
