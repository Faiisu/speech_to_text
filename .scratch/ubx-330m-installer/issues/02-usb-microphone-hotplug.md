# 02. Support USB microphone hotplug

Status: ready-for-agent

## Request

Allow USB ALSA capture devices connected after the application container starts to be discovered and used without recreating the container.

## Acceptance criteria

- [x] Mount host `/dev/snd` as a directory bind mount instead of a startup-time Docker device mapping.
- [x] Add a Docker device cgroup rule allowing read/write access to character devices at ALSA major 116 with any minor number.
- [x] Add the host `audio` group GID to the container independently of whether a capture device exists when installation runs.
- [x] Keep `/dev/dri` GPU device mapping and other service behavior unchanged.
- [x] Document the hotplug behavior and its host ALSA/group prerequisites in `docs/deployment.md`.
- [x] Run no deployment on the developer workstation or UBX-330M. Use focused static validation only.

## Non-goals

- Do not make the container privileged or expose all of `/dev`.
- Do not add an automatic udev restart hook or periodic microphone polling; microphone enumeration already occurs when the device-list endpoint is requested.
