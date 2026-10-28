#!/usr/bin/env bash
# Ensure Go >= 1.20 before the pinned Telegram WEB relay installer runs.
# Prefer an OS-supported, automatically patched package over a direct tarball.
# When OS packages are too old, use a checksum-verified upstream release.
set -Eeuo pipefail

is_usable_go() {
  local version minor
  command -v go >/dev/null 2>&1 || return 1
  version="$(go env GOVERSION 2>/dev/null)" || return 1
  [[ "$version" =~ ^go1\.([0-9]+)(\.|$) ]] || return 1
  minor="${BASH_REMATCH[1]}"
  (( minor >= 20 ))
}

if is_usable_go; then
  printf '[Go] Compatible toolchain already installed: %s\n' "$(go version)"
  exit 0
fi

echo "[Go] Installing Go from operating-system packages (recommended)..."
if DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends golang-go; then
  if is_usable_go; then
    echo "[Go] OS packaged toolchain is compatible: $(go version)"
    exit 0
  fi
  echo "[Go] OS packaged Go is older than 1.20; trying a checksum-verified official archive."
else
  echo "[Go] golang-go apt package unavailable; trying a checksum-verified official archive."
fi

# Pinned release and SHA-256 are from the official go.dev/dl release manifest.
version="1.26.5"
checksum="5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053"
name="go${version}.linux-amd64.tar.gz"
target="/opt/go${version}"
if [[ -x "$target/bin/go" ]]; then
  export PATH="$target/bin:$PATH"
  if is_usable_go; then
    echo "[Go] Reusing installed verified toolchain: $(go version)"
    exit 0
  fi
fi
archive="$(mktemp /tmp/blue-go.XXXXXX.tar.gz)"
directory="$(mktemp -d /tmp/blue-go-extract.XXXXXX)"
trap 'rm -f "$archive"; rm -rf "$directory"' EXIT
sources=(
  "https://go.dev/dl/$name"
  "https://storage.googleapis.com/golang/$name"
  "https://mirrors.aliyun.com/golang/$name"
)
success=0
for url in "${sources[@]}"; do
  echo "[Go] Trying $url"
  if ! curl --fail --silent --show-error --location --retry 2 --retry-delay 2 \
      --connect-timeout 12 --max-time 180 --proto '=https' \
      --proto-redir '=https' --tlsv1.2 --output "$archive" "$url"; then
    echo "[Go] Download failed; testing another source." >&2
    continue
  fi
  actual="$(sha256sum "$archive" | cut -d ' ' -f 1)"
  if [[ "$actual" != "$checksum" ]]; then
    echo "[Go] SHA-256 mismatch; rejecting $url" >&2
    continue
  fi
  success=1
  break
done
if [[ "$success" != 1 ]]; then
  echo "[Go] ERROR: no valid Go >=1.20 toolchain could be installed. Check apt and HTTPS connectivity." >&2
  exit 1
fi

tar -C "$directory" -xzf "$archive"
[[ -x "$directory/go/bin/go" ]] || { echo "[Go] Invalid extracted archive"; exit 1; }
if [[ ! -e "$target" ]]; then
  mv "$directory/go" "$target"
fi
export PATH="$target/bin:$PATH"
is_usable_go || { echo "[Go] Installed Go toolchain cannot run"; exit 1; }
echo "[Go] Installed checksum-verified Go toolchain: $(go version)"
