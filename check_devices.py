"""Check whether hot-plugged microphones are actually detected on this machine.

The GUI's "Rescan" button re-initialises PortAudio to pick up a device
connected after the server started. Whether that works depends on the audio
stack — notably on Linux, where PulseAudio/PipeWire can hide individual
hardware devices behind a single aggregate entry. Run this to find out what
your machine actually does:

    uv run python check_devices.py
"""

import sounddevice as sd


def input_devices() -> dict[int, dict]:
    hostapis = sd.query_hostapis()
    return {
        index: {
            "name": device["name"],
            "hostapi": hostapis[device["hostapi"]]["name"]
            if device["hostapi"] < len(hostapis)
            else "unknown",
        }
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] > 0
    }


def show(devices: dict[int, dict]) -> None:
    if not devices:
        print("  (none)")
    for index, device in devices.items():
        print(f"  [{index}] {device['name']}  via {device['hostapi']}")


def main() -> None:
    print("Host APIs on this machine:")
    for hostapi in sd.query_hostapis():
        print(f"  - {hostapi['name']}")

    print("\nInput devices now:")
    before = input_devices()
    show(before)

    print(
        "\nNow plug in (or unplug) a USB mic, or connect/disconnect a Bluetooth\n"
        "headset. Wait for the desktop to register it, then press Enter."
    )
    input()

    # exactly what the server's Rescan button does
    sd._terminate()
    sd._initialize()

    after = input_devices()
    print("Input devices after rescan:")
    show(after)

    added = {i: d for i, d in after.items() if d["name"] not in {x["name"] for x in before.values()}}
    removed = {i: d for i, d in before.items() if d["name"] not in {x["name"] for x in after.values()}}

    print()
    if added:
        print("Appeared after rescan:")
        show(added)
    if removed:
        print("Disappeared after rescan:")
        show(removed)

    if not added and not removed:
        print("No change detected.")
        print(
            "\nIf you did connect something, this machine doesn't expose it to\n"
            "PortAudio as its own device — common when audio goes through\n"
            "PulseAudio/PipeWire, which presents one aggregate input instead of\n"
            "each piece of hardware. In that case pick the aggregate device\n"
            "(often named 'pulse' or 'default') in the GUI and choose the actual\n"
            "microphone at the OS level instead:\n"
            "    pactl list short sources\n"
            "    pactl set-default-source <source-name>\n"
            "    # or use a GUI: pavucontrol"
        )
    else:
        print("\nHot-plug detection works here — the Rescan button will pick this up.")

    # indices can be reassigned on Linux when hardware changes; the GUI tracks
    # the selected device by name for this reason
    moved = {
        index: after[index]["name"]
        for index in set(before) & set(after)
        if before[index]["name"] != after[index]["name"]
    }
    if moved:
        print("\nNote: these device indices now point at a different device:")
        for index, name in moved.items():
            print(f"  [{index}] {before[index]['name']!r} -> {name!r}")


if __name__ == "__main__":
    main()
