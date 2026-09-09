# Locate uv, and export UV as an absolute path.
#
# Sourced rather than run: both install.sh and preflight.sh need this, and
# they need it before anything else works.
#
# uv installs itself to ~/.local/bin, which a login shell picks up from
# ~/.profile. Neither sudo nor systemd reads that: sudo replaces PATH with
# `secure_path` from /etc/sudoers, and a systemd unit starts with a bare
# default PATH. So `command -v uv` succeeds when you test it by hand and fails
# in exactly the two contexts this deployment uses.
find_uv() {
    if command -v uv > /dev/null 2>&1; then
        command -v uv
        return 0
    fi
    # SUDO_USER is who invoked sudo — their home is where uv actually lives.
    _owner="${SUDO_USER:-$(id -un)}"
    _home="$(getent passwd "$_owner" 2>/dev/null | cut -d: -f6)"
    _home="${_home:-$HOME}"
    for _candidate in \
        "$_home/.local/bin/uv" \
        "$_home/.cargo/bin/uv" \
        /usr/local/bin/uv \
        /opt/homebrew/bin/uv
    do
        if [ -x "$_candidate" ]; then
            echo "$_candidate"
            return 0
        fi
    done
    return 1
}

UV="$(find_uv)" || {
    echo "uv is not installed, or not where this script can find it." >&2
    echo "Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    echo "Already installed? Pass its path:  UV=/path/to/uv $0" >&2
    exit 1
}
export UV
