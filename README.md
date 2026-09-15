# Falcata Joystick

ASUS ROG Falcata analog key reader and virutal joystick.

ASUS ROG Falcata has analog keys, but no official way to actually use it as analog input - this repo attempts to hack together a workaround.

The latency of the virtual controller is not great, due to limits in how keys are read in the keyboard - but should work for more casual games.

Many games also do not support mixed gamepad + keyboard + mouse input very well. For example Decima engine games (Horizon, Death Stranding) or Pragmata.

## Web test

Open the [standalone web page](https://dschlyter.github.io/falcata-joystick/hid-keyboard.html) to try it out (Chrome or Edge, source in [hid-keyboard.html](hid-keyboard.html)).

Note: This is effectively a **key logger**, which will log keys from your keyboard after you connect, even on other sites. All data stays locally, but to be sure close the tab before you do anything sensitive.

## Python

Python script using [vgamepad](https://github.com/yannbouteiller/vgamepad) to simulate an actual gamepad from your keyboard.

### Installation

Requires Windows. Run the following in PowerShell.

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), a Python package manager. It also installs Python if needed:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. Clone or download this repo and open a terminal in its folder.
3. Run `uv run falcata_joystick.py` - it should install gamepad driver on first install.

You can go to [Gamepad Tester](https://hardwaretester.com/gamepad) to try it out.

### Usage

```powershell
uv run falcata_joystick.py --no-gamepad  # check that key travel is read (prints stick values)
uv run falcata_joystick.py               # WASD -> left stick
uv run falcata_joystick.py --arrows      # arrows -> right stick
uv run falcata_joystick.py --both        # both sticks (slower polling)
uv run falcata_joystick.py --block-keys  # tracked keys no longer send normal key presses
uv run falcata_joystick.py --map-w R --map-s L  # W -> right trigger, S -> left trigger (still analog)
```

Run `uv run falcata_joystick.py --help` for all options.

Many games switch back and forth between gamepad and keyboard+mouse whenever they see both at once. `--block-keys` stops the tracked keys from also reaching the game as key presses, so it only sees the gamepad. You can't type those keys anywhere while it runs.

Turn it off before you play any online multiplayer game as anti-cheat might not like the virtual controller being present.