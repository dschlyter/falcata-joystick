# /// script
# requires-python = ">=3.10"
# dependencies = ["hidapi", "vgamepad"]
# ///
"""ROG Falcata analog keys -> virtual Xbox 360 controller.

Usage:
    uv run falcata_joystick.py              # WASD = left stick, arrows = right stick
    uv run falcata_joystick.py --no-gamepad # print stick values only (no ViGEmBus needed)

Python port of hid-keyboard.html. The firmware only reports travel for one key
at a time, so we round-robin "select key" commands on the control collection
and read the travel reports from the event collection.
"""

import argparse
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

WASD = {"W": 18, "A": 31, "S": 32, "D": 33}
ARROWS = {"Up": 83, "Down": 84, "Left": 79, "Right": 89}
OPPOSITE = {18: 32, 32: 18, 31: 33, 33: 31, 83: 84, 84: 83, 79: 89, 89: 79}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dwell-ms", type=int, default=10, help="time spent reading each polled key")
    parser.add_argument("--no-arrows", action="store_true", help="only poll WASD (faster)")
    parser.add_argument("--no-gamepad", action="store_true", help="print values instead of driving a gamepad")
    args = parser.parse_args()

    keys = list(WASD.values()) + ([] if args.no_arrows else list(ARROWS.values()))
    control, event = open_keyboard()
    output = PrintOutput() if args.no_gamepad else GamepadOutput()
    reader = TravelReader(control, event, keys, args.dwell_ms / 1000)

    print("Listening - press WASD or arrow keys (Ctrl+C to quit)")
    try:
        while True:
            reader.step()
            output.update(reader.travel)
    except KeyboardInterrupt:
        pass
    finally:
        control.close()
        event.close()


class TravelReader:
    """Two modes, as in the web page:
    - idle: "single key mode", firmware reports whichever key is pressed
    - polling: explicitly select each analog key in turn, prioritising active keys
    """

    def __init__(self, control, event, keys, dwell_s):
        self.control = control
        self.event = event
        self.keys = keys
        self.dwell_s = dwell_s
        self.travel = {k: 0 for k in keys}
        self.last_seen = {k: 0.0 for k in keys}
        self.last_activity = 0.0
        self.polling = False
        self.select_key(None)

    def step(self):
        if not self.polling:
            self.read_reports(0.05)
            return

        if time.monotonic() - self.last_activity > IDLE_TIMEOUT_S:
            self.polling = False
            self.travel = {k: 0 for k in self.keys}
            self.select_key(None)
            return

        for key in poll_sequence(self.keys, self.travel):
            self.select_key(key)
            self.read_reports(self.dwell_s)
        self.expire_stale_keys()

    def read_reports(self, duration_s):
        deadline = time.monotonic() + duration_s
        while (remaining_ms := int((deadline - time.monotonic()) * 1000)) > 0:
            report = self.event.read(64, remaining_ms)
            if report:
                self.on_report(report)

    def on_report(self, report):
        parsed = parse_travel_report(report)
        if not parsed or parsed[0] not in self.travel:
            return
        key, value = parsed
        now = time.monotonic()
        if self.polling and value == 0:
            return  # released keys are zeroed by expire_stale_keys
        self.travel[key] = value
        self.last_seen[key] = now
        if value > 0:
            self.last_activity = now
            self.polling = True

    def expire_stale_keys(self):
        stale_s = self.dwell_s * len(self.keys) * 3
        now = time.monotonic()
        for key, value in self.travel.items():
            if value > 0 and now - self.last_seen[key] > stale_s:
                self.travel[key] = 0

    def select_key(self, key):
        """key=None enables single key mode."""
        payload = [] if key is None else [key & 0xFF, key >> 8]
        write_command(self.control, 0x51, 0x61, payload)


def poll_sequence(keys, travel):
    """Interleave active keys between each inactive key, so held keys update most often.
    Inactive keys whose opposite is held are skipped (can't press both W and S)."""
    active = [k for k in keys if travel[k] > 0]
    if not active:
        return keys
    inactive = [k for k in keys if travel[k] == 0 and OPPOSITE[k] not in active]
    if not inactive:
        return active
    return [k for idle_key in inactive for k in [*active, idle_key]]


class GamepadOutput:
    def __init__(self):
        import vgamepad

        self.pad = vgamepad.VX360Gamepad()

    def update(self, travel):
        lx, ly, rx, ry = stick_values(travel)
        self.pad.left_joystick_float(lx, ly)
        self.pad.right_joystick_float(rx, ry)
        self.pad.update()


class PrintOutput:
    def __init__(self):
        self.last = None

    def update(self, travel):
        values = stick_values(travel)
        if values != self.last:
            lx, ly, rx, ry = values
            print(f"L({lx:+.2f}, {ly:+.2f})  R({rx:+.2f}, {ry:+.2f})")
            self.last = values


def stick_values(travel):
    """Returns (lx, ly, rx, ry) in -1..1, y up positive (XInput convention)."""
    def axis(neg, pos):
        return max(-1.0, min(1.0, (travel.get(pos, 0) - travel.get(neg, 0)) / MAX_TRAVEL))

    return (
        axis(WASD["A"], WASD["D"]),
        axis(WASD["S"], WASD["W"]),
        axis(ARROWS["Left"], ARROWS["Right"]),
        axis(ARROWS["Down"], ARROWS["Up"]),
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


if __name__ == "__main__":
    main()
