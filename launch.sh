#!/bin/sh
# Verified bootstrapper for the offline planner.
#
# It fetches one pinned repository archive, checks its SHA-256 before any
# extraction, refuses unsafe archive members, then runs planning/preview.py from
# the verified tree with an isolated Python interpreter. It never pipes remote
# bytes into a shell and never calls the TrueNAS middleware.
#
# Usage:
#   launch.sh (--revision COMMIT | --archive FILE) --sha256 HEX [--] [PREVIEW_ARGS...]
#
#   --revision COMMIT  40-character lowercase commit; downloads its GitHub archive over HTTPS.
#   --archive FILE     local .tar.gz (for offline tests and manual review); still digest-checked.
#   --sha256 HEX       expected SHA-256 of the archive; required, no default.
set -eu

REPO_OWNER="Noclasss"
REPO_NAME="truenas-linux-vm-installer"
MAX_ARCHIVE_BYTES=52428800

die() {
    printf 'launch.sh: %s\n' "$1" >&2
    exit "${2:-1}"
}

usage() {
    die "usage: launch.sh (--revision COMMIT | --archive FILE) --sha256 HEX [--] [PREVIEW_ARGS...]" 2
}

revision=""
archive_src=""
expected_sha256=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --revision)
            [ "$#" -ge 2 ] || usage
            revision=$2
            shift 2
            ;;
        --archive)
            [ "$#" -ge 2 ] || usage
            archive_src=$2
            shift 2
            ;;
        --sha256)
            [ "$#" -ge 2 ] || usage
            expected_sha256=$2
            shift 2
            ;;
        --)
            shift
            break
            ;;
        -*)
            usage
            ;;
        *)
            break
            ;;
    esac
done

# Exactly one source; the digest is mandatory and must be well-formed before
# any network or filesystem work happens.
if [ -n "$revision" ] && [ -n "$archive_src" ]; then
    usage
fi
if [ -z "$revision" ] && [ -z "$archive_src" ]; then
    usage
fi
if [ -z "$expected_sha256" ]; then
    die "--sha256 is required; refusing to run an unverified archive" 2
fi
case "$expected_sha256" in
    *[!0-9a-f]*|"") die "--sha256 must be 64 lowercase hexadecimal characters" 2 ;;
esac
if [ "${#expected_sha256}" -ne 64 ]; then
    die "--sha256 must be 64 lowercase hexadecimal characters" 2
fi
if [ -n "$revision" ]; then
    case "$revision" in
        *[!0-9a-f]*|"") die "--revision must be a 40-character lowercase commit" 2 ;;
    esac
    if [ "${#revision}" -ne 40 ]; then
        die "--revision must be a 40-character lowercase commit" 2
    fi
fi

for tool in sha256sum tar awk python3; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool missing: $tool"
done

# Private working area; mktemp creates it mode 0700. Everything under it is
# removed on exit, including on signals.
tmp_root=${TMPDIR:-/tmp}
work=$(mktemp -d "$tmp_root/linux-vm-launch.XXXXXX")
cleanup() {
    rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

archive="$work/archive.tar.gz"

if [ -n "$revision" ]; then
    command -v curl >/dev/null 2>&1 || die "required tool missing: curl"
    url="https://github.com/$REPO_OWNER/$REPO_NAME/archive/$revision.tar.gz"
    # HTTPS only, TLS verification left on, bounded size. Output stays in the
    # private work area until the digest matches.
    curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' --tlsv1.2 \
        --max-filesize "$MAX_ARCHIVE_BYTES" \
        --output "$archive" "$url" || die "download failed for the pinned revision"
else
    [ -f "$archive_src" ] || die "archive not found: $archive_src"
    # Copy first, then hash and extract the copy, so the checked bytes are the
    # bytes that get used.
    cp -- "$archive_src" "$archive" || die "could not read archive"
fi

actual_sha256=$(sha256sum "$archive" | awk '{print $1}')
if [ "$actual_sha256" != "$expected_sha256" ]; then
    die "archive SHA-256 mismatch; nothing was extracted or run"
fi

# Reject anything other than regular files and directories, and any absolute or
# parent-relative member name, before extraction.
tar -tzf "$archive" > "$work/names" || die "archive is not a readable gzip tar"
tar -tvzf "$archive" > "$work/listing" || die "archive is not a readable gzip tar"
awk '$1 !~ /^[-d]/ { bad = 1 } END { exit bad }' "$work/listing" \
    || die "archive contains a link or special file; refusing to extract"
while IFS= read -r member; do
    case "$member" in
        /*|../*|*/../*|*/..|..)
            die "archive contains an unsafe member path; refusing to extract"
            ;;
    esac
done < "$work/names"

tree="$work/tree"
mkdir -- "$tree"
tar -xzf "$archive" -C "$tree" --strip-components=1 \
    --no-same-owner --no-same-permissions || die "extraction failed"

planner="$tree/planning/preview.py"
[ -f "$planner" ] || die "verified archive does not contain planning/preview.py"

# -E: ignore PYTHON* environment overrides; -s: no user site packages. The
# script's own directory stays on sys.path so preview.py can import its modules.
set +e
python3 -E -s "$planner" "$@"
status=$?
set -e
exit "$status"
