# /// script
# requires-python = ">=3.10"
# dependencies = ["hidapi", "vgamepad"]
# ///
"""ROG Falcata analog keys -> virtual Xbox 360 controller.

Usage:
    uv run falcata_joystick.py              # WASD = left stick (default, same as --wasd)
    uv run falcata_joystick.py --arrows     # arrows = right stick
    uv run falcata_joystick.py --both       # both sticks, slower polling
    uv run falcata_joystick.py --map-w R --map-s L  # W = right trigger, S = left trigger
    uv run falcata_joystick.py --no-gamepad # print stick values only (no ViGEmBus needed)

Python port of hid-keyboard.html. The firmware only reports travel for one key
at a time, so we round-robin "select key" commands on the control collection
and read the travel reports from the event collection.
"""

import argparse
import ctypes
import sys
import time

import hid

ASUS_VID = 0x0B05
CONTROL_USAGE_PAGES = {0xFF00, 0xFF01, 0xFF02, 0xFF03}
EVENT_USAGE_PAGES = {0xFFC0, 0xFFC1, 0xFFC2, 0xFFC3}
# Shown as "rpt=N" in the web page status. hidapi can't tell us this.
CONTROL_REPORT_ID = 0

TRAVEL_REPORT = 0x7E
MAX_TRAVEL = 350  # 0.01 mm units
IDLE_TIMEOUT_S = 5.0
MAX_EXTRAPOLATE_S = 0.05

WASD = {"W": 18, "A": 31, "S": 32, "D": 33}
ARROWS = {"Up": 83, "Down": 84, "Left": 79, "Right": 89}
OPPOSITE = {18: 32, 32: 18, 31: 33, 33: 31, 83: 84, 84: 83, 79: 89, 89: 79}
# firmware key code -> Windows virtual key code
VIRTUAL_KEY = {18: 0x57, 31: 0x41, 32: 0x53, 33: 0x44, 83: 0x26, 84: 0x28, 79: 0x25, 89: 0x27}
KEY_GROUPS = {
    "wasd": [list(WASD.values())],
    "arrows": [list(ARROWS.values())],
    "both": [list(WASD.values()), list(ARROWS.values())],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    keys = parser.add_mutually_exclusive_group()
    keys.add_argument("--wasd", dest="keys", action="store_const", const="wasd", help="track WASD only (default)")
    keys.add_argument("--arrows", dest="keys", action="store_const", const="arrows", help="track arrows only")
    keys.add_argument("--both", dest="keys", action="store_const", const="both", help="track WASD and arrows")
    parser.set_defaults(keys="wasd")
    parser.add_argument("--dwell-ms", type=int, default=10, help="time spent reading each polled key")
    parser.add_argument("--no-gamepad", action="store_true", help="print values instead of driving a gamepad")
    parser.add_argument("--block-keys", action="store_true",
                        help="stop tracked keys reaching other apps as normal key presses (Windows only)")
    parser.add_argument("--map-w", choices=["L", "R"], help="W drives left/right trigger instead of the stick")
    parser.add_argument("--map-s", choices=["L", "R"], help="S drives left/right trigger instead of the stick")
    parser.add_argument("--allow-opposite", action="store_true",
                        help="keep polling the opposite key while one is held, e.g. S while W is pressed (slower)")
    args = parser.parse_args()

    triggers = {}  # "L"/"R" -> firmware key code
    for side, key in [(args.map_w, WASD["W"]), (args.map_s, WASD["S"])]:
        if side in triggers:
            parser.error("--map-w and --map-s can't use the same trigger")
        if side:
            triggers[side] = key
    if triggers and args.keys == "arrows":
        parser.error("--map-w/--map-s need WASD tracking (--wasd or --both)")
    opposite = {} if args.allow_opposite else OPPOSITE

    if sys.platform == "win32":
        # Windows timers tick every ~15.6ms by default, too coarse for a 10ms dwell (browsers raise this too)
        ctypes.windll.winmm.timeBeginPeriod(1)

    control, event = open_keyboard()
    output = PrintOutput() if args.no_gamepad else GamepadOutput()
    groups = KEY_GROUPS[args.keys]
    reader = TravelReader(control, event, groups, opposite, args.dwell_ms / 1000)
    if args.block_keys:
        block_keys({VIRTUAL_KEY[k] for g in groups for k in g})

    print(f"Listening - press {args.keys} keys (Ctrl+C to quit)")
    try:
        while True:
            reader.step()
            output.update(gamepad_values(reader.current_travel(), triggers))
    except KeyboardInterrupt:
        pass
    finally:
        control.close()
        event.close()


class TravelReader:
    """Two modes, as in the web page:
    - idle: "single key mode", firmware reports whichever key is pressed
    - polling: explicitly select one key per step, see poll_sequence for the order
    """

    def __init__(self, control, event, groups, opposite, dwell_s):
        self.control = control
        self.event = event
        self.groups = groups
        self.opposite = opposite
        self.keys = [k for g in groups for k in g]
        self.dwell_s = dwell_s
        self.travel = {k: 0 for k in self.keys}
        self.velocity = {k: 0.0 for k in self.keys}  # travel units per second
        self.last_seen = {k: 0.0 for k in self.keys}
        self.last_activity = 0.0
        self.polling = False
        self.queue = []
        self.select_key(None)

    def step(self):
        if not self.polling:
            self.read_reports(0.05)
            return

        if time.perf_counter() - self.last_activity > IDLE_TIMEOUT_S:
            self.polling = False
            self.travel = {k: 0 for k in self.keys}
            self.queue = []
            self.select_key(None)
            return

        if not self.queue:
            self.expire_stale_keys()
            self.queue = poll_sequence(self.groups, self.travel, self.opposite)
        self.select_key(self.queue.pop(0))
        self.read_reports(self.dwell_s)

    def current_travel(self):
        """Extrapolate held keys along their last velocity, smoothing the gaps between polls."""
        if not self.polling:
            return dict(self.travel)
        now = time.perf_counter()
        return {
            k: v if v == 0 else clamp(v + self.velocity[k] * min(now - self.last_seen[k], MAX_EXTRAPOLATE_S), 0, MAX_TRAVEL)
            for k, v in self.travel.items()
        }

    def read_reports(self, duration_s):
        deadline = time.perf_counter() + duration_s
        while (remaining_ms := int((deadline - time.perf_counter()) * 1000)) > 0:
            report = self.event.read(64, remaining_ms)
            if report:
                self.on_report(report)

    def on_report(self, report):
        parsed = parse_travel_report(report)
        if not parsed or parsed[0] not in self.travel:
            return
        key, value = parsed
        if self.polling and value == 0:
            return  # released keys are zeroed by expire_stale_keys
        now = time.perf_counter()
        if value > 0:
            dt = now - self.last_seen[key]
            if dt > 0.001:
                self.velocity[key] = (value - self.travel[key]) / dt
            self.last_activity = now
            self.polling = True
        self.travel[key] = value
        self.last_seen[key] = now

    def expire_stale_keys(self):
        stale_s = self.dwell_s * len(self.keys) * 3
        now = time.perf_counter()
        for key, value in self.travel.items():
            if value > 0 and now - self.last_seen[key] > stale_s:
                self.travel[key] = 0

    def select_key(self, key):
        """key=None enables single key mode."""
        payload = [] if key is None else [key & 0xFF, key >> 8]
        write_command(self.control, 0x51, 0x61, payload)


def poll_sequence(groups, travel, opposite=OPPOSITE):
    """One polling cycle. Tricks from the web version to keep held keys responsive:
    - with both groups enabled, only poll the group(s) being pressed
    - interleave held keys between each idle key, so they update most often
    - skip keys whose opposite is held (no point reading S while W is pressed)
    """
    active_groups = [g for g in groups if any(travel[k] for k in g)]
    keys = [k for g in (active_groups or groups) for k in g]
    active = [k for k in keys if travel[k] > 0]
    if not active:
        return keys
    idle = [k for k in keys if travel[k] == 0 and opposite.get(k) not in active]
    if not idle:
        return active
    return [k for idle_key in idle for k in [*active, idle_key]]


class GamepadOutput:
    def __init__(self):
        import vgamepad

        self.pad = vgamepad.VX360Gamepad()
        self.last = None

    def update(self, values):
        if values == self.last:
            return
        lx, ly, rx, ry, lt, rt = values
        self.pad.left_joystick_float(lx, ly)
        self.pad.right_joystick_float(rx, ry)
        self.pad.left_trigger_float(lt)
        self.pad.right_trigger_float(rt)
        self.pad.update()
        self.last = values


class PrintOutput:
    def __init__(self):
        self.last = None

    def update(self, values):
        if values == self.last:
            return
        lx, ly, rx, ry, lt, rt = values
        # overwrite one line, scrolling the Windows console is slow
        print(f"L({lx:+.2f}, {ly:+.2f})  R({rx:+.2f}, {ry:+.2f})  LT {lt:.2f}  RT {rt:.2f}", end="\r", flush=True)
        self.last = values


def block_keys(virtual_keys):
    """Swallow key presses with a low-level keyboard hook, so games only see the gamepad.
    Analog travel is read from a separate HID collection and is unaffected."""
    if sys.platform != "win32":
        sys.exit("--block-keys is only supported on Windows")
    import threading
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = wintypes.LPARAM
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    @HOOKPROC
    def on_key(n_code, w_param, l_param):
        # l_param points to KBDLLHOOKSTRUCT, whose first field is the virtual key code
        if n_code == 0 and ctypes.cast(l_param, ctypes.POINTER(wintypes.DWORD))[0] in virtual_keys:
            return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def run():
        # Must stay responsive: Windows silently removes hooks that take too long (~300ms)
        WH_KEYBOARD_LL = 13
        if not user32.SetWindowsHookExW(WH_KEYBOARD_LL, on_key, kernel32.GetModuleHandleW(None), 0):
            print(f"Failed to block keys: {ctypes.WinError(ctypes.get_last_error())}")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    threading.Thread(target=run, daemon=True).start()


def gamepad_values(travel, triggers):
    """triggers maps "L"/"R" to the key driving that trigger; those keys don't move the sticks.
    Returns (lx, ly, rx, ry, lt, rt): sticks -1..1 with y up positive (XInput convention), triggers 0..1."""
    on_stick = {k: v for k, v in travel.items() if k not in triggers.values()}

    def axis(neg, pos):
        return clamp((on_stick.get(pos, 0) - on_stick.get(neg, 0)) / MAX_TRAVEL, -1.0, 1.0)

    def trigger(side):
        return clamp(travel.get(triggers.get(side), 0) / MAX_TRAVEL, 0.0, 1.0)

    return (
        axis(WASD["A"], WASD["D"]),
        axis(WASD["S"], WASD["W"]),
        axis(ARROWS["Left"], ARROWS["Right"]),
        axis(ARROWS["Down"], ARROWS["Up"]),
        trigger("L"),
        trigger("R"),
    )


def open_keyboard():
    collections = hid.enumerate(ASUS_VID, 0)
    control = find_collection(collections, CONTROL_USAGE_PAGES)
    event = find_collection(collections, EVENT_USAGE_PAGES)
    if not control or not event:
        print("Could not find Falcata control/event HID collections. ASUS collections found:")
        for c in collections:
            print(f"  {c['product_string']} usage_page=0x{c['usage_page']:04X} usage={c['usage']} path={c['path']}")
        sys.exit(1)
    return open_path(control["path"]), open_path(event["path"])


def find_collection(collections, usage_pages):
    return next((c for c in collections if c["usage_page"] in usage_pages and c["usage"] in (0, 1)), None)


def open_path(path):
    device = hid.device()
    device.open_path(path)
    return device


def parse_travel_report(report):
    """WebHID strips the report ID, hidapi may not - accept 0x7E at offset 0 or 1."""
    if report[0] != TRAVEL_REPORT:
        report = report[1:]
    if len(report) < 5 or report[0] != TRAVEL_REPORT:
        return None
    return report[1] | report[2] << 8, report[3] | report[4] << 8


def write_command(device, cmd, sub, payload):
    # hidapi write() takes the report ID as first byte; the web page used 64 bytes for id 0, else 63
    size = 64 if CONTROL_REPORT_ID == 0 else 63
    body = [cmd, sub, 0, 0, *payload]
    device.write([CONTROL_REPORT_ID, *body, *[0] * (size - len(body))])


def clamp(value, low, high):
    return max(low, min(high, value))


if __name__ == "__main__":
    main()
