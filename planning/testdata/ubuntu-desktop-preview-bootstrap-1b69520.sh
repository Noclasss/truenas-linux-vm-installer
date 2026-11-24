#!/bin/sh
set -eu
umask 077

repository_url='https://github.com/Noclasss/truenas-linux-vm-installer.git'

fail() {
    printf 'Desktop operator bootstrap refused: %s\n' "$1" >&2
    exit 1
}

[ "$#" -ge 3 ] && [ "$1" = '--ref' ] || fail 'usage: --ref FULL_COMMIT_SHA --preview | --ref FULL_COMMIT_SHA --create-only [settings]'
reviewed_commit=$2
shift 2
mode=$1
shift
case "$mode" in
    --preview) [ "$#" -eq 0 ] || fail '--preview accepts no additional arguments' ;;
    --create-only) ;;
    *) fail 'choose --preview or --create-only' ;;
esac
case "$reviewed_commit" in
    *[!0-9a-f]*|'') fail 'the source revision must be a full lowercase 40-character commit SHA' ;;
esac
[ "${#reviewed_commit}" -eq 40 ] || fail 'the source revision must be a full lowercase 40-character commit SHA'

command -v git >/dev/null 2>&1 || fail 'git is required'
command -v python3 >/dev/null 2>&1 || fail 'python3 is required'

git_config_count=${GIT_CONFIG_COUNT:-0}
case "$git_config_count" in
    ''|*[!0-9]*) fail 'invalid inherited Git config count' ;;
esac
[ "${#git_config_count}" -le 4 ] && [ "$git_config_count" -le 1024 ] || fail 'inherited Git config count is too large'
git_config_index=0
while [ "$git_config_index" -lt "$git_config_count" ]; do
    unset "GIT_CONFIG_KEY_$git_config_index" "GIT_CONFIG_VALUE_$git_config_index"
    git_config_index=$((git_config_index + 1))
done
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_TEMPLATE_DIR GIT_EXEC_PATH GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CONFIG_KEY_0 GIT_CONFIG_KEY_1 GIT_CONFIG_KEY_2 GIT_CONFIG_KEY_3 GIT_CONFIG_KEY_4 GIT_CONFIG_KEY_5 GIT_CONFIG_KEY_6 GIT_CONFIG_KEY_7 GIT_CONFIG_KEY_8 GIT_CONFIG_KEY_9 GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1 GIT_CONFIG_VALUE_2 GIT_CONFIG_VALUE_3 GIT_CONFIG_VALUE_4 GIT_CONFIG_VALUE_5 GIT_CONFIG_VALUE_6 GIT_CONFIG_VALUE_7 GIT_CONFIG_VALUE_8 GIT_CONFIG_VALUE_9 GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL GIT_CONFIG GIT_PROXY_COMMAND GIT_SSL_NO_VERIFY GIT_ASKPASS GIT_SSH GIT_SSH_COMMAND GIT_SSH_VARIANT GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM SSH_ASKPASS SSH_AUTH_SOCK SSH_AGENT_PID GIT_TRACE GIT_TRACE_CURL GIT_TRACE_PACKET GIT_TRACE_SETUP GIT_TRACE2 GIT_TRACE2_EVENT GIT_TRACE2_PERF || true
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
repo="$work_dir/repo"
printf 'Temporary pinned checkout: %s (removed on exit)\n' "$repo" >&2

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
case "$mode" in
    --preview) [ -f "$repo/planning/desktop_readonly_preview.py" ] || fail 'the reviewed guided preflight is missing' ;;
    --create-only) [ -f "$repo/planning/desktop_guided_operator.py" ] || fail 'the reviewed guided create operator is missing' ;;
esac

PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE
if [ "$mode" = '--preview' ]; then
    printf 'Running guided read-only Ubuntu Desktop preflight from %s; no create/start action is exposed.\n' "$reviewed_commit" >&2
    python3 "$repo/planning/desktop_readonly_preview.py" --preview
else
    printf 'Running the guided create-only operator from %s; VM creation remains behind its exact plan confirmation and the VM will stay stopped.\n' "$reviewed_commit" >&2
    cd "$repo"
    python3 -B -m planning.desktop_guided_operator "$@"
fi
