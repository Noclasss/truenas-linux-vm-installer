#!/bin/sh
set -eu
umask 077

repository_url='https://github.com/Noclasss/truenas-linux-vm-installer.git'
reviewed_commit='912333b58bdaec01a82bb9365212cf5642cc748b'

fail() {
    printf 'Desktop preview bootstrap refused: %s\n' "$1" >&2
    exit 1
}

if [ "$#" -ne 0 ]; then
    fail 'this entry point accepts no arguments; use a reviewed local checkout for other preview inputs'
fi
command -v git >/dev/null 2>&1 || fail 'git is required'
command -v python3 >/dev/null 2>&1 || fail 'python3 is required'

unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_TEMPLATE_DIR GIT_EXEC_PATH GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL GIT_CONFIG GIT_PROXY_COMMAND GIT_SSL_NO_VERIFY GIT_ASKPASS GIT_SSH GIT_SSH_COMMAND GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM SSH_ASKPASS || true
GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null
GIT_TERMINAL_PROMPT=0
GIT_LFS_SKIP_SMUDGE=1
export GIT_CONFIG_NOSYSTEM GIT_CONFIG_GLOBAL GIT_TERMINAL_PROMPT GIT_LFS_SKIP_SMUDGE

work_dir=$(mktemp -d "/tmp/linux-vm-desktop-preview.XXXXXXXX") || fail 'cannot create a private temporary directory'
cleanup() {
    rm -rf -- "$work_dir"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
chmod 700 "$work_dir" || fail 'cannot protect the temporary directory'
repo="$work_dir/repo"

if ! git -c core.hooksPath=/dev/null init --quiet "$repo"; then
    fail 'cannot initialize the temporary checkout'
fi
if ! git -c core.hooksPath=/dev/null -C "$repo" remote add origin "$repository_url"; then
    fail 'cannot configure the reviewed source'
fi
if ! git -c core.hooksPath=/dev/null -c http.sslVerify=true -c http.followRedirects=false -C "$repo" fetch --quiet --depth 1 --no-tags origin "$reviewed_commit"; then
    fail 'cannot fetch the reviewed source commit'
fi
if ! git -c core.hooksPath=/dev/null -C "$repo" checkout --quiet --detach FETCH_HEAD; then
    fail 'cannot check out the reviewed source commit'
fi
actual_commit=$(git -c core.hooksPath=/dev/null -C "$repo" rev-parse --verify HEAD) || fail 'cannot verify the checkout'
[ "$actual_commit" = "$reviewed_commit" ] || fail 'the fetched commit does not match the reviewed pin'
[ -x "$repo/install.sh" ] || fail 'the reviewed local launcher is missing'

PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE
printf '%s\n' 'Offline Ubuntu Desktop preview only; this does not query TrueNAS or create/start a VM.' >&2
"$repo/install.sh" --interactive --profile ubuntu-desktop
