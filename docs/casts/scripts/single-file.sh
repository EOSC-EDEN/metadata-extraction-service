#!/usr/bin/env bash
# Demo: extract metadata from a FLAC file (metaflac), synchronously and as an async job.
# Needs a running MES (default http://localhost:8000) with the local rule for FLAC from
# service/config/tool_rules.yaml enabled (flac-audio -> metaflac), curl and python3.
set -euo pipefail

MES_URL="${MES_URL:-http://localhost:8000}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

curl -fsS -m 5 "$MES_URL/api/v1/limits/" >/dev/null || { echo "MES not reachable at $MES_URL" >&2; exit 1; }

# Minimal valid FLAC (STREAMINFO, Vorbis tags, one silent frame) so the recording does not
# depend on any local file or encoder.
python3 -I - <<'PY'
import struct

def crc(data, poly, bits):
    c, top, mask = 0, 1 << (bits - 1), (1 << bits) - 1
    for b in data:
        c ^= b << (bits - 8)
        for _ in range(8):
            c = ((c << 1) ^ poly) & mask if c & top else (c << 1) & mask
    return c

def block(last, kind, data):
    return bytes([(last << 7) | kind]) + len(data).to_bytes(3, "big") + data

header = bytes([0xFF, 0xF8, 0xC9, 0x08, 0x00])
frame = header + bytes([crc(header, 0x07, 8)]) + b"\x00\x00\x00"
frame += struct.pack(">H", crc(frame, 0x8005, 16))
streaminfo = struct.pack(">HH", 4096, 4096) + len(frame).to_bytes(3, "big") * 2
streaminfo += ((44100 << 44) | (15 << 36) | 4096).to_bytes(8, "big") + bytes(16)
vendor, tags = b"mes-demo", [b"TITLE=MES demo tone", b"ARTIST=EOSC EDEN"]
comment = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(tags))
comment += b"".join(struct.pack("<I", len(t)) + t for t in tags)
open("sample.flac", "wb").write(b"fLaC" + block(0, 0, streaminfo) + block(1, 4, comment) + frame)
PY

type_cmd() { printf '\n$ %s\n' "$*"; sleep 0.7; eval "$*" || true; sleep 1.5; }

# Reads an extraction result (JSON) on stdin: format, rule, per-tool status.
cat > summarise.py <<'PY'
import json, sys
d = json.load(sys.stdin)
tr = d["tool_resolution"]
print(f'file: {d["filename"]}  PUID: {tr["puid"]}  MIME: {tr["mime_type"]}')
print(f'selected by: {tr["selection_method"]}  rules: {", ".join(tr["matched_rules"]) or "-"}')
for r in d["results"]:
    print(f'  {r["tool"]:<10} {r["status"]}')
print(f'errors: {len(d["errors"])}  warnings: {len(d["warnings"])}')
PY

# Reads an extraction result (JSON) on stdin: the unmodified metaflac output.
cat > metaflac.py <<'PY'
import json, sys
r = next(r for r in json.load(sys.stdin)["results"] if r["tool"] == "metaflac")
print(" ".join(r["command"][:2]), "...")
print("\n".join(l for l in r["raw_output"].splitlines() if any(k in l for k in ("sample_rate", "channels", "bits-per-sample", "total samples", "comment["))))
PY

echo "# Metadata Extraction Service: FLAC extraction with metaflac"
sleep 1

echo; echo "# 1. Which tools are enabled?"
type_cmd "curl -s $MES_URL/api/v1/tools/ | python3 -I -c 'import json,sys; [print(t[\"name\"], \"enabled=\"+str(t[\"enabled\"])) for t in json.load(sys.stdin)[\"tools\"]]'"

echo; echo "# 2. What are the deployment limits?"
type_cmd "curl -s $MES_URL/api/v1/limits/; echo"

echo; echo "# 3. Which local format rules are configured?"
type_cmd "curl -s $MES_URL/api/v1/local-rules/; echo"

echo; echo "# 4. Extract metadata synchronously: the FLAC rule selects metaflac"
type_cmd "curl -s -F file=@sample.flac $MES_URL/api/v1/extract/ | python3 -I summarise.py"

echo; echo "# 5. The raw metaflac output is returned unmodified"
type_cmd "curl -s -F file=@sample.flac $MES_URL/api/v1/extract/ | python3 -I metaflac.py"

echo; echo "# 6. Ask for XML instead"
type_cmd "curl -s -H 'Accept: application/xml' -F file=@sample.flac $MES_URL/api/v1/extract/ | head -c 300; echo ..."

echo; echo "# 7. Asynchronous: submit a job, poll it, fetch the result"
JOB_ID="$(curl -s -F file=@sample.flac "$MES_URL/api/v1/jobs/" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')"
type_cmd "curl -s $MES_URL/api/v1/jobs/$JOB_ID/"
for _ in $(seq 20); do
  [ "$(curl -s "$MES_URL/api/v1/jobs/$JOB_ID/" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["status"])')" = succeeded ] && break
  sleep 1
done
type_cmd "curl -s $MES_URL/api/v1/jobs/$JOB_ID/result/ | python3 -I summarise.py"
