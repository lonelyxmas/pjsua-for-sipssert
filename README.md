# pjsua-test

[Русская версия](README.ru.md)

A PJSIP/PJSUA2-based Docker image for testing SIP over TLS with RTP/SRTP validation.
Designed to be used with [SIPssert](https://github.com/OpenSIPS/SIPssert) as a replacement for SIPp
in scenarios where SIPp has architectural limitations.

## Motivation

SIPp has several issues that cannot be resolved without patching the source code
(status re-checked against v3.8.0):

| Problem | Root cause in SIPp | Solution in pjsua-test | SIPp 3.8.0 |
|---|---|---|---|
| Poor SRTP validation performance | Custom JLSRTP implementation: excessive `std::vector` allocations, global mutexes, no zero-copy | PJSIP uses libsrtp2 with in-place encryption | ⚠️ improved (AES/HMAC set up once per stream instead of per packet: 1/6 less CPU, 1/3 less memory), but still JLSRTP, not libsrtp |
| TLS role is tied to SIP role | UAC = TLS client, UAS = TLS server, hardcoded in `sslsocket.cpp` | PJSUA2 API allows arbitrary role combinations | ❌ not fixed: `SSL_connect` is still bound to the outgoing socket and `SSL_accept` to accepted ones (`socket.cpp`) |
| Global SRTP contexts | Single key set for all calls | Per-call SRTP contexts | ✅ fixed: per-call SRTP keys (#1002), per-call `rtp_echo` |
| No DTLS-SRTP | SDES only | PJSIP supports both SDES and DTLS-SRTP | ❌ the core is still SDES-only; DTLS-SRTP exists only as an external Python probe in `tools/` |

## Building

```bash
docker build -t pjsua-test .
```

The image is based on Alpine 3.24 with prebuilt `pjproject` 2.17 and `py3-yaml` packages — build takes ~5 seconds.

## Operating Modes

All 4 SIP/TLS role combinations:

| Mode | SIP role | TLS role | Description |
|---|---|---|---|
| `--mode=uac` | UAC (caller) | Client | Standard: initiates call and TLS connection |
| `--mode=uas` | UAS (callee) | Server | Standard: listens on port, accepts calls |
| `--mode=uas-tls-client` | UAS (callee) | Client | Initiates TLS connection, waits for incoming INVITE |
| `--mode=uac-tls-server` | UAC (caller) | Server | Listens on TLS port, sends INVITE after client connects |

The `uas-tls-client` and `uac-tls-server` modes are impossible in SIPp — this is the key advantage.

All 4 modes are implemented using the PJSUA2 Python API (`scripts/*.py`).
The `uac` and `uas` modes support all 3 transports (TLS, TCP, UDP) via the `--transport` parameter.
TLS-role modes (`uas-tls-client`, `uac-tls-server`) always use TLS regardless of `--transport`.

## Configuration File

Parameters can be passed via a YAML file: `--config=/home/config.yml`.
CLI arguments take precedence over the config; header lists (`headers:`) are merged.

**YAML config format (all supported keys):**

```yaml
mode: uac             # uac | uas | uas-tls-client | uac-tls-server
transport: tls        # tls | tcp | udp (for uac/uas; TLS-role modes ignore this)
proxy: 127.0.0.1:5061 # remote side address
port: 5061            # local SIP/TLS port
ip: 0.0.0.0           # bind to specific IP / interface
rtp_port: 16000       # local RTP port (for host networking)
dest_uri: ""          # full SIP URI (instead of proxy)
ruri_user: test       # user part of Request-URI
from_user: uac        # user part of From header
duration: 10          # call duration, sec
tolerance: 90         # min match % for PASS (echo validation)
min_received_pct: 60  # min % of sent frames that must arrive (0 = off)
wait_timeout: 30      # timeout for incoming call, sec (uas-tls-client)
tls_wait: 10          # timeout for TLS connection, sec (uac-tls-server)
srtp: mandatory       # off | optional | mandatory
srtp_secure: 0        # 0 = none, 1 = TLS required, 2 = end-to-end
log_level: 3          # PJSIP log level 0-6
bye: uac              # uac | uas | none (which side sends BYE)
wait_bye: 30          # timeout for waiting BYE from remote side
reinvite_by: uac      # uac | uas (which side sends re-INVITE)
reinvite_delay: "3"   # delay(s) in seconds, comma-separated: "3" or "3,7,12"
options_ping: 5       # in-dialog OPTIONS send interval, sec
options_auto_reply: true   # only reply to OPTIONS (don't send)
options_tolerance: 90      # min % of successful OPTIONS responses

tls:
  ca_file: /home/certs/ca.pem       # CA certificate
  cert_file: /home/certs/cert.pem   # client/server certificate
  privkey_file: /home/certs/key.pem # private key
  verify_server: false              # verify server certificate
  verify_client: false              # verify client certificate

headers:
  set:                  # add headers to outgoing requests
    - "X-My-Header: value"
  expect:               # verify header presence in response
    - "X-Response-Header"
  expect_not:           # verify header absence
    - "X-Forbidden-Header"
  expect_name_regex:    # at least one header name matches regex
    - "^X-Custom-.*"
  expect_not_regex:     # no header name matches regex
    - "^X-Bad-.*"
  expect_value:         # exact header value match (supports indexing)
    - "X-Response-Header: expected-value"
    - "Via[0]: SIP/2.0/TLS"
  expect_value_regex:   # header value matches regex
    - "X-Session-Id: [0-9a-f]{8}"
  expect_count:         # header occurrence count (N, N+, N-M)
    - "Via: 2"    # exactly 2
    - "Via: 2+"   # at least 2
    - "Via: 1-3"  # 1 to 3
```

**Usage with sipssert:**

```yaml
args:
  - "--config=/home/config.yml"
  - "--duration=5"          # CLI overrides config
  - "--expect-header=X-Extra"  # header lists are merged
```

## Test Scenarios

### Scenario 1: Basic SIP over TLS

Verifies SIP signaling over TLS.

```
pjsua-test (UAC, TLS client)          SIPp (UAS, TLS server)
     |                                        |
     |-------- TLS ClientHello ------------->|
     |<------- TLS ServerHello --------------|
     |                                        |
     |-------- INVITE ---------------------->|
     |<------- 200 OK -----------------------|
     |-------- ACK ------------------------->|
     |         ... duration ...               |
     |-------- BYE ------------------------->|
```

**sipssert scenario.yml:**

```yaml
tasks:
  - name: sipp-uas
    type: sipp
    config_file: uas.xml
    daemon: true
    args:
      - "-t l1"

  - name: pjsua-uac
    image: pjsua-test
    require:
      - { started: sipp-uas }
    args:
      - "--mode=uac"
      - "--proxy=sipp-uas:5061"
      - "--tls"
      - "--tls-ca-file=/home/certs/ca.pem"
      - "--duration=5"
```

### Scenario 2: SIP over TLS + SRTP with Echo Validation

Verifies TLS signaling + SRTP media integrity. SIPp runs `rtp_echo`, pjsua-test sends
a known pattern and performs byte-by-byte comparison of the returned data.

```
pjsua-test (UAC)                     SIPp (UAS, rtp_echo)
     |                                        |
     |-------- INVITE (crypto in SDP) ------>|
     |<------- 200 OK (crypto in SDP) -------|
     |-------- ACK ------------------------->|
     |                                        |
     |== SRTP (known pattern) ==============>|
     |<= SRTP (echo, same encrypted bytes) ==|
     |                                        |
     | [decrypt + compare payload]            |
     |                                        |
     |-------- BYE ------------------------->|
     |                                        |
     | exit 0 (match >= tolerance)            |
     | exit 1 (match < tolerance)             |
```

**sipssert scenario.yml:**

```yaml
tasks:
  - name: sipp-uas-echo
    type: sipp
    config_file: uas_srtp_echo.xml
    daemon: true
    args:
      - "-t l1"
      - "-rtp_echo"

  - name: pjsua-uac-srtp
    image: pjsua-test
    require:
      - { started: sipp-uas-echo }
    args:
      - "--mode=uac"
      - "--proxy=sipp-uas-echo:5061"
      - "--tls"
      - "--tls-ca-file=/home/certs/ca.pem"
      - "--srtp=mandatory"
      - "--duration=5"
```

### Scenario 3: SIP UAS + TLS Client (Role Decoupling)

A scenario impossible in SIPp. pjsua-test initiates a TLS connection to the remote server
(TLS client) while waiting for an incoming INVITE (SIP UAS).

Typical use case: testing an SBC or proxy where the client side must connect via TLS
but still accept incoming calls.

```
pjsua-test (SIP UAS, TLS client)    SIPp/SBC (SIP UAC, TLS server)
     |                                        |
     |-------- TLS ClientHello ------------->|  pjsua-test initiates TLS
     |<------- TLS ServerHello --------------|
     |<------- TLS Handshake done -----------|
     |                                        |
     |<------- INVITE -----------------------|  SIPp sends call
     |-------- 200 OK ---------------------->|  pjsua-test answers
     |<------- ACK --------------------------|
     |                                        |
     |== SRTP (pattern) ===================>|
     |<= SRTP (echo) =======================|
     |                                        |
     | [echo validation]                      |
     |                                        |
     |-------- BYE (after duration) -------->|
```

**sipssert scenario.yml:**

```yaml
tasks:
  - name: sipp-uac-echo
    type: sipp
    config_file: uac_srtp_echo.xml
    daemon: true
    args:
      - "-t l1"
      - "-rtp_echo"

  - name: pjsua-uas-tls-client
    image: pjsua-test
    require:
      - { started: sipp-uac-echo }
    args:
      - "--mode=uas-tls-client"
      - "--proxy=sipp-uac-echo:5061"
      - "--port=15062"
      - "--rtp-port=17000"
      - "--tls-ca-file=/home/certs/ca.pem"
      - "--tls-cert-file=/home/certs/client.pem"
      - "--tls-privkey-file=/home/certs/client-key.pem"
      - "--srtp=mandatory"
      - "--duration=5"
      - "--tolerance=85"
```

### Scenario 4: SIP UAC + TLS Server (Reverse Role Decoupling)

Mirror of scenario 3. pjsua-test listens on a TLS port (TLS server), waits for an incoming
TLS connection from the remote side, then sends an INVITE (SIP UAC).

TLS connection detection: the remote side sends a probe INVITE, which uac-tls-server
uses as a readiness signal.

```
pjsua-test (SIP UAC, TLS server)    SIPp/Device (SIP UAS, TLS client)
     |                                        |
     |<------- TLS ClientHello --------------|  Remote side connects
     |-------- TLS ServerHello ------------->|
     |-------- TLS Handshake done ---------->|
     |                                        |
     |-------- INVITE ---------------------->|  pjsua-test sends call
     |<------- 200 OK -----------------------|
     |-------- ACK ------------------------->|
     |                                        |
     |== SRTP (pattern) ===================>|
     |<= SRTP (echo) =======================|
     |                                        |
     | [echo validation]                      |
     |                                        |
     |-------- BYE (after duration) -------->|
```

**sipssert scenario.yml:**

```yaml
tasks:
  - name: pjsua-uac-tls-server
    image: pjsua-test
    daemon: true
    args:
      - "--mode=uac-tls-server"
      - "--proxy=sipp-uas:5060"
      - "--port=15061"
      - "--rtp-port=16000"
      - "--tls-cert-file=/home/certs/server.pem"
      - "--tls-privkey-file=/home/certs/server-key.pem"
      - "--srtp=mandatory"
      - "--duration=5"
      - "--tls-wait=10"
      - "--tolerance=85"

  - name: sipp-uas
    type: sipp
    config_file: uas_srtp_echo.xml
    require:
      - { started: pjsua-uac-tls-server }
    args:
      - "-t l1"
      - "-rtp_echo"
```

### Scenario 5: Two pjsua-test Instances with Role Decoupling (Integration)

A proven integration test — two pjsua-test containers communicate with each other
with full SIP/TLS role decoupling. The test is located in `tests/pjsua-tls-roles/`.

```
pjsua-test (SIP UAC, TLS server)    pjsua-test (SIP UAS, TLS client)
     |                                        |
     | [listening TLS:15061]                  |
     |<------- TLS connect ------------------|  UAS connects as TLS client
     |<------- probe INVITE (rejected) ------|  Readiness signal
     |                                        |
     |-------- INVITE ---------------------->|  UAC sends call
     |<------- 200 OK -----------------------|
     |-------- ACK ------------------------->|
     |                                        |
     |<======= SRTP media ==================>|
     |                                        |
     |-------- BYE (after 5s) -------------->|
```

**sipssert scenario.yml:**

```yaml
tasks:
  - name: uac-tls-server
    image: pjsua-test
    daemon: true
    args:
      - "--mode=uac-tls-server"
      - "--proxy=127.0.0.1:15062"
      - "--port=15061"
      - "--rtp-port=16000"
      - "--tls-cert-file=/home/certs/cacert.pem"
      - "--tls-privkey-file=/home/certs/cakey.pem"
      - "--srtp=mandatory"
      - "--srtp-secure=0"
      - "--duration=5"
      - "--tls-wait=15"
      - "--tolerance=0"

  - name: uas-tls-client
    image: pjsua-test
    require:
      - { started: uac-tls-server }
    args:
      - "--mode=uas-tls-client"
      - "--proxy=127.0.0.1:15061"
      - "--port=15062"
      - "--rtp-port=17000"
      - "--tls-ca-file=/home/certs/cacert.pem"
      - "--tls-cert-file=/home/certs/cacert.pem"
      - "--tls-privkey-file=/home/certs/cakey.pem"
      - "--srtp=mandatory"
      - "--srtp-secure=0"
      - "--duration=10"
      - "--tolerance=0"
      - "--wait-timeout=20"
```

Running:

```bash
pip install git+https://github.com/OpenSIPS/SIPssert.git
sipssert tests/
```

### Scenario 6: SBC/Proxy Testing (End-to-End)

Full test: two pjsua-test instances on both ends with an SBC/proxy in between.
Verifies that the SBC correctly proxies TLS signaling and SRTP media.

```
pjsua-test (UAC)      SBC/Proxy        pjsua-test (UAS)
     |                    |                    |
     |--- INVITE -------->|--- INVITE -------->|
     |<-- 200 OK ---------|<-- 200 OK ---------|
     |--- ACK ----------->|--- ACK ----------->|
     |                    |                    |
     |== SRTP ==========>|== SRTP ==========>|
     |<= SRTP (echo) ====|<= SRTP (echo) ====|
     |                    |                    |
     | [validation]       |                    |
```

**sipssert scenario.yml:**

```yaml
tasks:
  - name: pjsua-uas
    image: pjsua-test
    daemon: true
    args:
      - "--mode=uas"
      - "--tls"
      - "--tls-cert-file=/home/certs/server.pem"
      - "--tls-privkey-file=/home/certs/server-key.pem"
      - "--srtp=mandatory"
      - "--duration=10"

  - name: proxy
    image: my-sbc
    require:
      - { started: pjsua-uas }

  - name: pjsua-uac
    image: pjsua-test
    require:
      - { started: proxy }
    args:
      - "--mode=uac"
      - "--proxy=proxy:5061"
      - "--tls"
      - "--tls-ca-file=/home/certs/ca.pem"
      - "--srtp=mandatory"
      - "--duration=5"
```

## RTP/SRTP Echo Validation

When used with `rtp_echo` on the remote side, pjsua-test performs pattern-based
media stream verification (per-frame envelope check — see *Matching on receive*
below):

1. `EchoValidatorPort` generates frames cycling through a short sequence of
   well-separated μ-law-stable values (the cycle fits into the 64-value ring
   buffer, which keeps matching immune to peer phase shifts after mid-call
   re-INVITE — see below)
2. PJSIP encrypts the frame (SRTP) and sends it over the network
3. The remote side (`rtp_echo`) returns the encrypted packet as-is
4. PJSIP decrypts the received packet with its own key
5. `EchoValidatorPort` compares the decrypted payload against a ring buffer of sent frames
6. Echo delay is accounted for: the ring holds the last 64 sent values,
   covering several full pattern cycles

Result:

```
==================================================
RTP/SRTP Echo Validation Results:
  Frames sent:       250
  Frames received:   248
  Frames matched:    247
  Frames mismatched: 1
  Match rate:        99.6%
  Tolerance:         90%
  RESULT: PASS
==================================================
```

Exit code: `0` = PASS, `1` = FAIL. Compatible with sipssert (exit code determines test result).

Exit code `0` requires **all** of the following:
- **Call completed** — call was established and terminated normally
- **Echo validation** — RTP match rate ≥ `--tolerance` (default 90%) and received
  rate ≥ `--min-received-pct` (default 60% of sent frames), or no media was expected
- **Header checks** — all `--expect-header*` checks passed (if configured)
- **OPTIONS ping** — success rate ≥ `--options-tolerance` (default 90%), if `--options-ping` is configured

If any condition fails, or the call fails to connect, exit code is `1`.

## Parameters

Both formats are supported: `--key=value` and `--key value`.

### General

| Parameter | Description | Default |
|---|---|---|
| `--mode` | `uac`, `uas`, `uas-tls-client`, `uac-tls-server` | `uac` |
| `--config PATH` | YAML config file (CLI arguments take precedence) | - |
| `--transport` | `tls`, `tcp`, `udp` (for uac/uas; TLS-role modes use TLS) | `tls` |
| `--proxy HOST:PORT` | Remote side address | - |
| `--port PORT` | Local SIP/TLS port | 5060 / 5061 |
| `--bind-ip ADDR` | Bind to a specific IP / interface | 0.0.0.0 |
| `--rtp-port PORT` | Local RTP port (for host networking) | auto |
| `--duration SEC` | Call duration | 10 |
| `--dest-uri URI` | Full SIP URI (instead of --proxy) | - |
| `--ruri-user USER` | User part of Request-URI | `test` |
| `--from-user USER` | User part of From header | `uac` / `uas` |

### TLS

| Parameter | Description |
|---|---|
| `--tls` | Enable TLS transport (uac/uas modes) |
| `--tls-ca-file PATH` | CA certificate |
| `--tls-cert-file PATH` | Client/server certificate |
| `--tls-privkey-file PATH` | Private key |
| `--tls-verify-server` | Verify server certificate |
| `--tls-verify-client` | Verify client certificate |

### SRTP

| Parameter | Description | Default |
|---|---|---|
| `--srtp` | `off`, `optional`, `mandatory` | `off` |
| `--srtp-secure` | 0 = no requirements, 1 = TLS required, 2 = end-to-end | 0 |

### RTP Echo Validation

| Parameter | Description | Default |
|---|---|---|
| `--tolerance` | Minimum match percentage for PASS | 90 |
| `--min-received-pct` | Minimum received rate (% of sent frames) for PASS; 0 disables | 60 |
| `--wait-timeout` | Timeout for incoming call, sec (uas-tls-client) | 30 |
| `--tls-wait` | Timeout for TLS connection, sec (uac-tls-server) | 10 |

**Trust boundary:** the validation proves **end-to-end media liveness and SRTP
decryptability** — audio actually flowed both ways, survived the codec, and
with `srtp: mandatory` decrypted with the correct key (a wrong key makes
libsrtp drop packets → silence → mismatch). It is **not** a byte-exact
content check: received frames are matched by median amplitude against the
peer's value set (attenuation window /12…+2%), so a different signal of
similar amplitude would still pass. Packet loss alone does not fail the test —
it lowers the received rate; the `--min-received-pct` floor (default 60%,
applied from ~1 s of audio) catches streams that die mid-call.

**How it works:**

Both sides independently generate deterministic audio frames using `EchoValidatorPort`. Each frame is filled with a single constant PCM value from a pre-built table of **μ-law-stable values** (range 500–20000). The value cycles through the table for each new frame:

```
Frame 0: all 160 samples = -508
Frame 1: all 160 samples =  508
Frame 2: all 160 samples = -524
...
```

Since both sides use the same generation algorithm starting from sequence 0, they produce identical frames.

**Data path:**

```
EchoValidatorPort → conference bridge → G.711 encode → RTP → network →
→ RTP → G.711 decode → conference bridge → EchoValidatorPort
```

The μ-law-stable values survive G.711 PCMU encode/decode without loss. However, the PJSUA2 conference bridge attenuates amplitude by approximately 3–12×.

**Matching on receive:**

1. Compute the **median** of all samples in the received frame
2. If `|median| < 10` — frame is treated as silence (mismatch)
3. Search the ring buffer (last 64 sent values) for a match: same **sign** and amplitude within proportional range (`|sent| / 12 ≤ |median| ≤ |sent| · 1.02` — the 2% upper headroom absorbs G.711 requantisation)

**Why a short pattern cycle:** a mid-call re-INVITE recreates the media
session and perturbs the relative frame counters of the two peers. With a
160-value pattern cycle the one-sided 64-value ring window flipped out of
range on any such perturbation and every subsequent frame was counted as a
mismatch. With the cycle fully covered by the ring, matching is immune to
constant phase offsets while still detecting silence, amplitude garbage and
codec corruption.

**Incoming re-INVITE under SRTP:** a re-INVITE offer without `a=crypto`
(e.g. relayed by an SBC/rtpengine without SRTP flags) is rejected with 488
before pjsua touches media state (RFC 3261 §14.2 — the previous session
stays in effect), and a re-anchor re-INVITE is sent 0.2 s later to
re-synchronise the media leg. Without this, pjproject 2.17 swaps the active
SRTP RX key on the failed renegotiation and inbound audio degrades to
silence for the rest of the call.

**Codec compatibility:**

| Codec | Status | Notes |
|---|---|---|
| PCMU (G.711 μ-law) | Tested, ~98% match | Values are μ-law fixed points |
| PCMA (G.711 A-law) | Expected to work | Same sign/amplitude preservation |
| Speex, Opus, G.722 | Expected to work | May need wider tolerance range |
| Codecs with DTX/silence suppression | May fail | Constant-fill frames can be treated as silence |

**Practical notes:**
- `--tolerance=0` disables validation (only checks call establishment)
- `--tolerance=90` (default) — recommended for pjsua↔pjsua and OpenSIPS relay tests
- First 2–3 frames may mismatch due to call setup latency (ring buffer not yet populated)
- If match rate is unexpectedly low, check for media manipulation (AGC, noise gate, transcoding) in the path

### BYE Control

| Parameter | Description | Default |
|---|---|---|
| `--bye` | `uac`, `uas`, `none` — which side sends BYE | Depends on mode |
| `--wait-bye` | Timeout for waiting BYE from remote side, sec | 30 |

By default, BYE is sent by the side that owns the call (`uac` for uac/uac-tls-server, `uas` for uas/uas-tls-client). With `--bye=none`, nobody sends BYE — the call lives for `--duration` seconds, then the script exits with the echo validation result.

If `--bye` points to the other side, the script waits for BYE from the remote side for up to `--wait-bye` seconds after `--duration` expires. If BYE is not received — exit 1.

### re-INVITE

| Parameter | Description | Default |
|---|---|---|
| `--reinvite-by` | `uac` or `uas` — which side sends re-INVITE | - |
| `--reinvite-delay` | Delay(s) after media establishment, comma-separated (sec) | - |

Both parameters are required together. Example: `--reinvite-by=uac --reinvite-delay=3` sends a re-INVITE after 3 seconds. Multiple re-INVITEs: `--reinvite-delay=3,7,12`.

After a re-INVITE, `EchoValidatorPort` reconnects to the new media object — counters are not reset, validation continues.

Both sides can be initiators (each specifies `--reinvite-by` for its own role). Stagger timers to avoid glare (SIP 491).

### In-dialog OPTIONS Ping

| Parameter | Description | Default |
|---|---|---|
| `--options-ping` | OPTIONS send interval, sec (enables auto-reply) | - |
| `--options-auto-reply` | Only reply 200 OK to incoming OPTIONS | false |
| `--options-tolerance` | Minimum % of successful OPTIONS responses | 90 |

The initiating side (`--options-ping=N`) sends in-dialog OPTIONS every N seconds and checks the percentage of received 200 OK responses. The other side (`--options-auto-reply`) only replies.

OPTIONS validation result affects the exit code along with echo validation and header checks.

### Custom SIP Headers

| Parameter | Value format | Description |
|---|---|---|
| `--set-header` | `Name: Value` | Add header to outgoing requests (repeatable) |
| `--expect-header` | `Name` | Verify header presence in response (repeatable) |
| `--expect-no-header` | `Name` | Verify header absence (repeatable) |
| `--expect-header-regex` | `REGEX` | At least one header name matches regex (repeatable) |
| `--expect-no-header-regex` | `REGEX` | No header name matches regex (repeatable) |
| `--expect-header-value` | `Name[N]: Value` | Exact header value match (repeatable) |
| `--expect-header-value-regex` | `Name[N]: REGEX` | Header value matches regex (repeatable) |
| `--expect-header-count` | `Name: N\|N+\|N-M` | Header occurrence count (repeatable) |

**Indexing**: `Name[0]` — first, `Name[-1]` — last header with that name.

**Count ranges**: `2` — exactly 2, `2+` — at least 2, `1-3` — 1 to 3.

**Example:**

```bash
--set-header="X-Request-Id: abc123"
--expect-header-value="X-Session: abc123"
--expect-header-value-regex="Via[0]: SIP/2.0/TLS .*"
--expect-header-count="Via: 2+"
--expect-no-header-regex="^X-Debug-.*"
```

All header checks are applied to the INVITE response (200 OK). A failed check results in exit code 1.

### Miscellaneous

| Parameter | Description |
|---|---|
| `--log-level N` | PJSIP log level 0-6 (default 3) |
| `PJSUA_EXTRA_ARGS` | Additional arguments via environment variable (legacy) |

## Project Structure

```
.
├── Dockerfile              # Alpine 3.24 + pjproject/pjsua/py3-pjsua/py3-yaml packages
├── entrypoint.sh           # CLI wrapper: parses arguments, launches the appropriate script
├── scripts/
│   ├── common.py           # Shared module: EchoValidatorPort, OptionsPingManager,
│   │                       # HeaderManager, ConfigLoader, BYE/re-INVITE/OPTIONS helpers
│   ├── uac.py              # SIP UAC + TLS Client + echo validation (PJSUA2)
│   ├── uas.py              # SIP UAS + TLS Server + echo validation (PJSUA2)
│   ├── uas_tls_client.py   # SIP UAS + TLS Client + echo validation (PJSUA2)
│   └── uac_tls_server.py   # SIP UAC + TLS Server + echo validation (PJSUA2)
├── tests/
│   ├── config.yml              # sipssert test set config
│   ├── pjsua-standard-roles/   # uac + uas (standard roles)
│   ├── pjsua-tls-roles/        # uac-tls-server + uas-tls-client
│   ├── pjsua-config-file/      # YAML config test (--config)
│   ├── pjsua-headers-set-check/    # --set-header + --expect-header-value
│   ├── pjsua-headers-expect-not/   # --expect-no-header
│   ├── pjsua-headers-regex/        # --expect-header-regex + --expect-header-count
│   ├── pjsua-headers-tls-roles/    # headers with TLS role decoupling
│   ├── pjsua-udp-transport/    # UDP transport (--transport=udp)
│   ├── pjsua-tcp-transport/    # TCP transport (--transport=tcp)
│   ├── pjsua-bye-from-uas/    # BYE from UAS side
│   ├── pjsua-bye-none/        # no BYE (both sides --bye=none)
│   ├── pjsua-reinvite-uac/    # re-INVITE from UAC
│   ├── pjsua-reinvite-uas/    # re-INVITE from UAS
│   └── pjsua-options-ping/    # in-dialog OPTIONS ping
└── README.md
```

## Known Issues

- **JACK/ALSA messages**: suppressed via `JACK_NO_START_SERVER=1` and null ALSA config.
  When using stale Docker cache, they may reappear — rebuild with `--no-cache`.
- **Segfault on exit**: PJSUA2 Python bindings sometimes crash during `libDestroy()`.
  Worked around via `os._exit()` — exit code is correct.
- **pjproject 2.17 (alpine ≥3.24) teardown**: `libDestroy()` takes ~1.5s and the
  uas-side `onCallState(DISCONNECTED)` callback arrives ~2s after BYE (on 2.14 —
  instant). Since sipssert SIGKILLs daemon containers (`docker stop(timeout=0)`)
  right after the client exits, shutdown must be fast: `wait_call_end()` polls
  the call state in addition to the event, `safe_shutdown()` skips
  `hangupAll()`/destroy for an already-disconnected call, and a SIGTERM handler
  (`install_signal_exit()`) turns `docker stop` into a clean exit 0 — note that
  PID 1 never receives default-disposition signals, so without the handler the
  container always dies with 137. Set `stop_timeout: 5` under
  `defaults: generic:` in the sipssert config so `docker stop` sends SIGTERM
  first. Two caveats: (1) this requires a sipssert version with
  defaults-by-type merge (`tasks_list.py` merges `defaults[task_type]`) — on
  older versions add `stop_timeout: 5` to each daemon task in `scenario.yml`
  instead; (2) SIGINT (Ctrl+C) is also converted to a silent exit 0 — no
  KeyboardInterrupt traceback when running the scripts manually.
- **Test cert validity horizon**: the 9 original scenario `certs/` dirs expire
  **2027-04-02**; the 5 regenerated ones (bye-from-uas, bye-none, options-ping,
  reinvite-uac, reinvite-uas) expire **2036**. Around April 2027 the suite
  will start failing with certificate errors — regenerate uniformly at commit
  time or beforehand.
- **In-dialog OPTIONS keepalive (`pjsua-options-ping` scenario) always fails**:
  pjsua2 cannot answer in-dialog OPTIONS — the dialog rejects the request with
  `500 Unhandled by dialog usages` (sip_dialog.c) before any app code runs, and
  pjsua2 exposes neither a callback for incoming in-dialog requests nor an API
  to respond to one (verified on 2.14.1, 2.17 and master). The
  `--options-auto-reply` flag is therefore non-functional; real-world PBX
  keepalives are out-of-dialog, which pjsua answers natively with 200.
- **Probe INVITE** (`uac-tls-server` mode): uas-tls-client sends a probe INVITE
  to signal TLS connection readiness. uac-tls-server rejects it (486 Busy)
  and then sends the real INVITE.

## License

This project is licensed under the GNU General Public License v2.0
or any later version (SPDX: `GPL-2.0-or-later`) — see the [LICENSE](LICENSE) file.

The Docker image also contains third-party software under its own licenses,
notably [pjproject / pjsua / py3-pjsua](https://github.com/pjsip/pjproject)
(GPL-2.0-or-later, © Teluu Inc.) installed from Alpine packages, and the
[Alpine Linux](https://alpinelinux.org/) base image and its packages under
their respective licenses.
