# Falcate Joystick

ASUS ROG Falcata analog key reader and virutal joystick.

ASUS ROG Falcata has analog keys, but no official way to actually use it as analog input - this repo attempts to hack together a solution.

The latency of the virtual controller is not great, due to limits in how keys are read in the keyboard - but should work for more casual games.

## Web test

Open the [standalone web page](hid-keyboard.html) to try it out.

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
```

Leave it running while you play. Dependencies are installed automatically on the first run.

Run `uv run falcata_joystick.py --help` for all options.

Turn it off before you play any online multiplayer game as anti-cheat might not like the virtual controller being present.