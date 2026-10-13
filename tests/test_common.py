#!/usr/bin/env python3
"""Unit tests for common.py — edge cases, None handling, defaults."""

import argparse
import struct
import sys
import threading
import unittest

sys.path.insert(0, "/scripts")
# Allow running outside Docker (pjsua2 not available)
try:
    import pjsua2 as pj
    _PJ_AVAILABLE = True
except ImportError:
    _PJ_AVAILABLE = False

from common import (
    _parse_bool,
    _apply_arg_defaults,
    _build_ulaw_quantize_table,
    _build_stable_values,
    _STABLE_VALUES,
    configure_srtp,
    ConfigLoader,
    HeaderManager,
)


# -----------------------------------------------------------------------
# _parse_bool
# -----------------------------------------------------------------------

class TestParseBool(unittest.TestCase):

    def test_true_variants(self):
        for v in ("true", "True", "TRUE", "yes", "Yes", "1"):
            self.assertTrue(_parse_bool(v), f"Expected True for {v!r}")

    def test_false_variants(self):
        for v in ("false", "False", "FALSE", "no", "No", "0"):
            self.assertFalse(_parse_bool(v), f"Expected False for {v!r}")

    def test_bool_passthrough(self):
        self.assertTrue(_parse_bool(True))
        self.assertFalse(_parse_bool(False))

    def test_invalid_string_raises(self):
        for v in ("maybe", "on", "off", "2", "yes!", ""):
            with self.assertRaises(argparse.ArgumentTypeError, msg=f"Should raise for {v!r}"):
                _parse_bool(v)

    def test_none_raises(self):
        with self.assertRaises((argparse.ArgumentTypeError, AttributeError)):
            _parse_bool(None)


# -----------------------------------------------------------------------
# _apply_arg_defaults
# -----------------------------------------------------------------------

class TestApplyArgDefaults(unittest.TestCase):

    def _make_args(self, **kwargs):
        args = argparse.Namespace(
            tls=False, transport=None, srtp=None, srtp_secure=None,
            duration=None, tolerance=None, wait_timeout=None,
            tls_wait=None, wait_bye=None, options_tolerance=None,
            reinvite_delay=None, reinvite_by=None,
        )
        for k, v in kwargs.items():
            setattr(args, k, v)
        return args

    def test_fills_none_with_defaults(self):
        args = self._make_args()
        _apply_arg_defaults(args)
        self.assertEqual(args.duration, 10)
        self.assertEqual(args.tolerance, 90.0)
        self.assertEqual(args.wait_timeout, 30)
        self.assertEqual(args.tls_wait, 10)
        self.assertEqual(args.wait_bye, 30)
        self.assertEqual(args.srtp, "off")
        self.assertEqual(args.srtp_secure, 0)
        self.assertEqual(args.transport, "tls")

    def test_explicit_values_not_overridden(self):
        args = self._make_args(duration=5, transport="udp", srtp="mandatory")
        _apply_arg_defaults(args)
        self.assertEqual(args.duration, 5)
        self.assertEqual(args.transport, "udp")
        self.assertEqual(args.srtp, "mandatory")

    def test_tls_flag_overrides_transport(self):
        args = self._make_args(tls=True, transport=None)
        _apply_arg_defaults(args)
        self.assertEqual(args.transport, "tls")

    def test_tls_flag_no_override_explicit_transport(self):
        args = self._make_args(tls=True, transport="tcp")
        _apply_arg_defaults(args)
        self.assertEqual(args.transport, "tcp")

    def test_reinvite_delay_parsing(self):
        args = self._make_args(reinvite_delay="3,7,12", reinvite_by="uac")
        _apply_arg_defaults(args)
        self.assertEqual(args.reinvite_delays, [3.0, 7.0, 12.0])

    def test_reinvite_delay_none(self):
        args = self._make_args(reinvite_delay=None)
        _apply_arg_defaults(args)
        self.assertEqual(args.reinvite_delays, [])

    def test_reinvite_delay_single(self):
        args = self._make_args(reinvite_delay="5", reinvite_by="uac")
        _apply_arg_defaults(args)
        self.assertEqual(args.reinvite_delays, [5.0])

    def test_zero_duration_preserved(self):
        """duration=0 is falsy but should NOT be replaced with default."""
        args = self._make_args(duration=0)
        _apply_arg_defaults(args)
        # 0 is not None, so _apply_arg_defaults should keep it
        self.assertEqual(args.duration, 0)


# -----------------------------------------------------------------------
# configure_srtp
# -----------------------------------------------------------------------

@unittest.skipUnless(_PJ_AVAILABLE, "pjsua2 not available")
class TestConfigureSrtp(unittest.TestCase):

    def test_none_srtp(self):
        acfg = pj.AccountConfig()
        configure_srtp(acfg, None, None)
        self.assertEqual(acfg.mediaConfig.srtpUse, pj.PJMEDIA_SRTP_DISABLED)
        self.assertEqual(acfg.mediaConfig.srtpSecureSignaling, 0)

    def test_empty_string_srtp(self):
        acfg = pj.AccountConfig()
        configure_srtp(acfg, "", 0)
        self.assertEqual(acfg.mediaConfig.srtpUse, pj.PJMEDIA_SRTP_DISABLED)

    def test_mandatory_srtp(self):
        acfg = pj.AccountConfig()
        configure_srtp(acfg, "mandatory", 1)
        self.assertEqual(acfg.mediaConfig.srtpUse, pj.PJMEDIA_SRTP_MANDATORY)
        self.assertEqual(acfg.mediaConfig.srtpSecureSignaling, 1)

    def test_unknown_srtp_value(self):
        acfg = pj.AccountConfig()
        configure_srtp(acfg, "invalid_value", 0)
        self.assertEqual(acfg.mediaConfig.srtpUse, pj.PJMEDIA_SRTP_DISABLED)

    def test_srtp_secure_zero_not_replaced(self):
        """srtp_secure=0 is valid and should not be treated as None."""
        acfg = pj.AccountConfig()
        configure_srtp(acfg, "off", 0)
        self.assertEqual(acfg.mediaConfig.srtpSecureSignaling, 0)


# -----------------------------------------------------------------------
# ConfigLoader.merge
# -----------------------------------------------------------------------

class TestConfigLoaderMerge(unittest.TestCase):

    def _make_args(self, **kwargs):
        args = argparse.Namespace(
            proxy=None, port=None, bind_ip=None, duration=None,
            srtp=None, srtp_secure=None, transport=None,
            tls_cert_file=None, tls_privkey_file=None,
            tls_ca_file=None, tls_verify_server=None,
            tls_verify_client=None, tolerance=None,
            wait_timeout=None, tls_wait=None, log_level=None,
            bye=None, wait_bye=None, dest_uri=None,
            rtp_port=None, options_ping=None,
            options_auto_reply=None, options_tolerance=None,
            reinvite_by=None, reinvite_delay=None,
            ruri_user=None, from_user=None,
        )
        for k, v in kwargs.items():
            setattr(args, k, v)
        return args

    def test_empty_config(self):
        args = self._make_args(duration=5)
        ConfigLoader.merge({}, args)
        self.assertEqual(args.duration, 5)

    def test_config_fills_none(self):
        args = self._make_args()
        ConfigLoader.merge({"duration": 20, "proxy": "1.2.3.4:5060"}, args)
        self.assertEqual(args.duration, 20)
        self.assertEqual(args.proxy, "1.2.3.4:5060")

    def test_cli_takes_precedence(self):
        args = self._make_args(duration=5)
        ConfigLoader.merge({"duration": 20}, args)
        self.assertEqual(args.duration, 5)

    def test_tls_config_section(self):
        args = self._make_args()
        config = {"tls": {"cert_file": "/cert.pem", "verify_server": True}}
        ConfigLoader.merge(config, args)
        self.assertEqual(args.tls_cert_file, "/cert.pem")
        self.assertEqual(args.tls_verify_server, True)

    def test_tls_config_none(self):
        args = self._make_args()
        config = {"tls": None}
        ConfigLoader.merge(config, args)
        # Should not crash

    def test_config_none_value(self):
        args = self._make_args()
        ConfigLoader.merge({"duration": None}, args)
        # None from config should not override None in args (both None)
        self.assertIsNone(args.duration)


# -----------------------------------------------------------------------
# HeaderManager
# -----------------------------------------------------------------------

class TestHeaderManager(unittest.TestCase):

    def test_empty_config(self):
        hm = HeaderManager({})
        self.assertFalse(hm.has_checks())

    def test_none_config(self):
        hm = HeaderManager(None)
        self.assertFalse(hm.has_checks())

    def test_check_headers_empty_message(self):
        hm = HeaderManager({"expect": ["Via"]})
        results = hm.check_headers("")
        self.assertTrue(len(results) > 0)
        self.assertFalse(results[0].passed)

    def test_expect_header_present(self):
        msg = "SIP/2.0 200 OK\r\nVia: SIP/2.0/TLS 1.2.3.4\r\nContent-Length: 0\r\n\r\n"
        hm = HeaderManager({"expect": ["Via"]})
        results = hm.check_headers(msg)
        self.assertTrue(results[0].passed)

    def test_expect_header_absent(self):
        msg = "SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n"
        hm = HeaderManager({"expect": ["X-Custom"]})
        results = hm.check_headers(msg)
        self.assertFalse(results[0].passed)

    def test_expect_no_header(self):
        msg = "SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n"
        hm = HeaderManager({"expect_not": ["X-Forbidden"]})
        results = hm.check_headers(msg)
        self.assertTrue(results[0].passed)

    def test_expect_no_header_but_present(self):
        msg = "SIP/2.0 200 OK\r\nX-Forbidden: yes\r\nContent-Length: 0\r\n\r\n"
        hm = HeaderManager({"expect_not": ["X-Forbidden"]})
        results = hm.check_headers(msg)
        self.assertFalse(results[0].passed)

    def test_expect_value_with_index(self):
        msg = "SIP/2.0 200 OK\r\nVia: first\r\nVia: second\r\n\r\n"
        hm = HeaderManager({"expect_value": ["Via[1]: second"]})
        results = hm.check_headers(msg)
        self.assertTrue(results[0].passed)

    def test_expect_value_negative_index(self):
        msg = "SIP/2.0 200 OK\r\nVia: first\r\nVia: second\r\n\r\n"
        hm = HeaderManager({"expect_value": ["Via[-1]: second"]})
        results = hm.check_headers(msg)
        self.assertTrue(results[0].passed)

    def test_expect_count_exact(self):
        msg = "SIP/2.0 200 OK\r\nVia: a\r\nVia: b\r\n\r\n"
        hm = HeaderManager({"expect_count": ["Via: 2"]})
        results = hm.check_headers(msg)
        self.assertTrue(results[0].passed)

    def test_expect_count_min(self):
        msg = "SIP/2.0 200 OK\r\nVia: a\r\nVia: b\r\nVia: c\r\n\r\n"
        hm = HeaderManager({"expect_count": ["Via: 2+"]})
        results = hm.check_headers(msg)
        self.assertTrue(results[0].passed)

    def test_expect_count_range(self):
        msg = "SIP/2.0 200 OK\r\nVia: a\r\nVia: b\r\n\r\n"
        hm = HeaderManager({"expect_count": ["Via: 1-3"]})
        results = hm.check_headers(msg)
        self.assertTrue(results[0].passed)

    def test_expect_count_fail(self):
        msg = "SIP/2.0 200 OK\r\nVia: a\r\n\r\n"
        hm = HeaderManager({"expect_count": ["Via: 2"]})
        results = hm.check_headers(msg)
        self.assertFalse(results[0].passed)


# -----------------------------------------------------------------------
# μ-law stable values
# -----------------------------------------------------------------------

class TestStableValues(unittest.TestCase):

    def test_stable_values_not_empty(self):
        self.assertTrue(len(_STABLE_VALUES) > 0)

    def test_no_zero_in_stable_values(self):
        self.assertNotIn(0, _STABLE_VALUES)

    def test_stable_values_in_range(self):
        for v in _STABLE_VALUES:
            self.assertGreaterEqual(abs(v), 500)
            self.assertLessEqual(abs(v), 20000)

    def test_stable_values_are_ulaw_fixed_points(self):
        """Each value should survive μ-law encode→decode unchanged."""
        table = _build_ulaw_quantize_table()
        for v in _STABLE_VALUES:
            unsigned = v & 0xFFFF
            stable = table[unsigned]
            # Convert back to signed for comparison
            stable_signed = stable if stable < 32768 else stable - 65536
            self.assertEqual(v, stable_signed,
                             f"Value {v} is not a μ-law fixed point")

    def test_stable_values_are_unique(self):
        self.assertEqual(len(_STABLE_VALUES), len(set(_STABLE_VALUES)))

    def test_has_positive_and_negative(self):
        """Both signs must be present: matching requires a same-sign bin
        for any received median, and signs alternate frame-to-frame."""
        pos = [v for v in _STABLE_VALUES if v > 0]
        neg = [v for v in _STABLE_VALUES if v < 0]
        self.assertGreaterEqual(len(pos), 5)
        self.assertGreaterEqual(len(neg), 5)

    def test_pattern_cycle_fits_ring_buffer(self):
        """Pattern cycle must fit entirely into the sent-ring window.

        Mid-call re-INVITE recreates the media session and shifts the
        relative frame counters of the two peers. If the ring (last 64
        sent values) covers the whole cycle, echo matching is immune to
        any constant phase offset between the peers. Otherwise a peer
        whose counter falls outside the one-sided window mismatches every
        frame until the end of the call.
        """
        # EchoValidatorPort.RING_SIZE без импорта pjsua2:
        # класс недоступен вне контейнера, значение фиксируем контрактом
        ring_size = 64
        self.assertLessEqual(
            len(_STABLE_VALUES), ring_size,
            f"pattern cycle ({len(_STABLE_VALUES)}) must fit "
            f"into the sent-ring window ({ring_size})")

    def test_pattern_magnitudes_well_separated(self):
        """Consecutive magnitudes must differ enough to distinguish bins
        under conference-bridge attenuation (matching window [v/12, v]).
        Each magnitude appears twice (both signs) — dedupe first.

        Пинним достигнутое разделение (≥1.3) с запасом над min_ratio=1.25
        из _select_pattern_values — при смене констант тест останется
        сторожем чувствительности бинов.
        """
        mags = sorted({abs(v) for v in _STABLE_VALUES})
        for prev, cur in zip(mags, mags[1:]):
            self.assertGreaterEqual(
                cur / prev, 1.3,
                f"magnitudes {prev} and {cur} are too close")


# -----------------------------------------------------------------------
# EchoValidatorPort (without pjsua2 runtime)
# -----------------------------------------------------------------------

@unittest.skipUnless(_PJ_AVAILABLE, "pjsua2 not available")
class TestEchoValidatorPort(unittest.TestCase):

    _ep = None

    @classmethod
    def setUpClass(cls):
        """Initialize PJSUA2 endpoint once for all tests."""
        ep = pj.Endpoint()
        ep_cfg = pj.EpConfig()
        ep_cfg.logConfig.level = 0
        ep_cfg.logConfig.consoleLevel = 0
        ep.libCreate()
        ep.libInit(ep_cfg)
        ep.audDevManager().setNullDev()
        ep.libStart()
        cls._ep = ep

    @classmethod
    def tearDownClass(cls):
        if cls._ep:
            try:
                cls._ep.libDestroy()
            except Exception:
                pass

    def _make_frame(self, size=320):
        class FakeFrame:
            pass
        f = FakeFrame()
        f.size = size
        f.buf = None
        f.type = 0
        return f

    def test_frame_generation_deterministic(self):
        from common import EchoValidatorPort
        v = EchoValidatorPort()
        v.register("test")

        f1 = self._make_frame()
        v.onFrameRequested(f1)
        data1 = bytes(f1.buf)

        f2 = self._make_frame()
        v.onFrameRequested(f2)
        data2 = bytes(f2.buf)

        self.assertEqual(len(data1), 320)
        self.assertEqual(len(data2), 320)
        self.assertNotEqual(data1, data2)

    def test_frame_constant_fill(self):
        """Each frame should be filled with a single constant value."""
        from common import EchoValidatorPort
        v = EchoValidatorPort()
        v.register("test2")

        f = self._make_frame()
        v.onFrameRequested(f)
        data = bytes(f.buf)
        samples = struct.unpack("<160h", data)
        self.assertEqual(len(set(samples)), 1, "Frame should have single constant value")

    def test_default_frame_size(self):
        """frame.size <= 0 should default to 320."""
        from common import EchoValidatorPort
        v = EchoValidatorPort()
        v.register("test3")

        f = self._make_frame(size=0)
        v.onFrameRequested(f)
        self.assertEqual(f.size, 320)

    @staticmethod
    def _received_frame(value):
        """Build a received frame filled with a single constant value."""
        f = TestEchoValidatorPort._make_frame_raw()
        samples = [value] * 160
        f.size = 320
        f.buf = pj.ByteVector(struct.pack("<160h", *samples))
        f.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
        return f

    @staticmethod
    def _make_frame_raw(size=320):
        class FakeFrame:
            pass
        f = FakeFrame()
        f.size = size
        f.buf = None
        f.type = 0
        return f

    def _reset_validator(self, v):
        """Reset pattern/counters state between test cases (one port)."""
        with v.lock:
            v.seq = 0
            v.sent_ring.clear()
            v.frames_sent = 0
            v.frames_received = 0
            v.frames_matched = 0
            v.frames_mismatched = 0

    def test_echo_match_immune_to_phase_shift(self):
        """Echo with arbitrary frame-phase offset must fully match.

        Regression test for mid-call re-INVITE: media session recreation
        perturbs relative frame counters, so the peer's stream may be an
        arbitrary number of frames ahead or behind. The ring window must
        cover the whole pattern cycle so any constant offset matches.
        """
        from common import EchoValidatorPort
        v = EchoValidatorPort()
        v.register("phase-test")

        n_send = 100   # fill the ring beyond RING_SIZE
        n_check = 40
        # offsets: in-window (control group), edge, and all the
        # out-of-window shifts that a re-INVITE transition can produce
        # (peer ahead or behind); relative to the last sent frame
        offsets = (0, 1, 5, 63, 64, 96, 159, -1, -5, -63, -64, -96, -159)
        cycle = len(_STABLE_VALUES)
        for offset in offsets:
            self._reset_validator(v)
            for _ in range(n_send):
                v.onFrameRequested(self._make_frame())
            for _ in range(n_check):
                with v.lock:
                    last_sent_seq = v.seq - 1
                peer_val = _STABLE_VALUES[(last_sent_seq + offset) % cycle]
                v.onFrameReceived(self._received_frame(peer_val))
            stats = v.get_stats()
            self.assertEqual(
                stats["mismatched"], 0,
                f"offset={offset}: {stats['mismatched']} frames mismatched")
            self.assertEqual(
                stats["matched"], n_check,
                f"offset={offset}: only {stats['matched']}/{n_check} matched")

    def test_corrupted_echo_frames_detected(self):
        """Negative test: validator MUST catch corrupted echo payloads.

        The phase-robustness fix must not turn the validator into a
        no-op: silence, garbage amplitude and near-silence noise are
        still counted as mismatches.
        """
        from common import EchoValidatorPort
        v = EchoValidatorPort()
        v.register("corrupt-test")

        for _ in range(80):
            v.onFrameRequested(self._make_frame())
        # healthy echo (peer mirrors the last frame we sent)
        for _ in range(10):
            with v.lock:
                last_sent_seq = v.seq - 1
            peer_val = _STABLE_VALUES[last_sent_seq % len(_STABLE_VALUES)]
            v.onFrameReceived(self._received_frame(peer_val))
        # corrupted payloads: each must be caught
        corruptions = (0, 30000, -30000, 20)  # silence, +/- clip, noise
        for value in corruptions:
            v.onFrameReceived(self._received_frame(value))

        stats = v.get_stats()
        self.assertEqual(stats["matched"], 10, str(stats))
        self.assertEqual(stats["mismatched"], len(corruptions), str(stats))

    def test_non_audio_frame_not_counted(self):
        """Acceptance criterion: added T.140 text media must not affect audio
        validation — the audio validator ignores non-audio frame types
        (e.g. PJMEDIA_FRAME_TYPE_NONE frames after text-stream negotiation)."""
        from common import EchoValidatorPort
        v = EchoValidatorPort()
        v.register("text-media-test")

        for _ in range(10):
            v.onFrameRequested(self._make_frame())

        f = self._make_frame_raw()
        f.size = 320
        f.buf = pj.ByteVector(struct.pack("<160h", *([500] * 160)))
        f.type = pj.PJMEDIA_FRAME_TYPE_NONE  # не аудио
        v.onFrameReceived(f)

        stats = v.get_stats()
        self.assertEqual(stats["received"], 0, str(stats))
        self.assertEqual(stats["matched"], 0, str(stats))
        self.assertEqual(stats["mismatched"], 0, str(stats))

    def test_echo_tolerates_codec_roundtrip_amplification(self):
        """G.711 a-law/μ-law round-trip can amplify values by ~1%.

        The largest bins must still match: the same-sign bin window is
        [v/12, v+2%], so a +1% requantisation shift does not fall out.
        """
        from common import EchoValidatorPort
        v = EchoValidatorPort()
        v.register("codec-headroom-test")

        for _ in range(80):
            v.onFrameRequested(self._make_frame())
        # every bin, shifted up by ~1% (worst-case codec amplification)
        for bin_val in _STABLE_VALUES:
            shifted = int(round(bin_val * 1.01)) or 1
            v.onFrameReceived(self._received_frame(shifted))
        stats = v.get_stats()
        self.assertEqual(stats["mismatched"], 0, str(stats))
        self.assertEqual(stats["matched"], len(_STABLE_VALUES), str(stats))


class _FakeRdata:
    def __init__(self, whole_msg):
        self.wholeMsg = whole_msg


class _FakePrm:
    def __init__(self, whole_msg):
        self.rdata = _FakeRdata(whole_msg)
        self.statusCode = 200


class _FakeApp:
    def __init__(self, srtp):
        self.args = argparse.Namespace(srtp=srtp)


class TestOnRxReinvite(unittest.TestCase):
    """on_rx_reinvite: reject crypto-less re-INVITE offers under SRTP.

    pjsua 2.17 swaps the active SRTP RX key even when the re-INVITE is
    answered 488, which kills inbound audio for the rest of the call.
    Rejecting BEFORE media re-init keeps the current session (RFC 3261
    §14.2).
    """

    OFFER_WITHOUT_CRYPTO = (
        "INVITE sip:x SIP/2.0\r\n\r\n"
        "v=0\r\no=- 1 2 IN IP4 10.0.0.1\r\n"
        "m=audio 10000 RTP/AVP 8 101\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
    )

    OFFER_WITH_CRYPTO = (
        "INVITE sip:x SIP/2.0\r\n\r\n"
        "v=0\r\no=- 1 2 IN IP4 10.0.0.1\r\n"
        "m=audio 10000 RTP/AVP 8 101\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
        "a=crypto:1 AES_256_CM_HMAC_SHA1_80 inline:abcd\r\n"
    )

    # re-INVITE, добавляющий текстовую медиа (T.140/red) поверх SRTP-аудио —
    # реальный кейс сценария 04 (оффер №2 от pbx, см. rtpengine-дамп)
    OFFER_WITH_CRYPTO_AND_TEXT = (
        "INVITE sip:x SIP/2.0\r\n\r\n"
        "v=0\r\no=- 1 2 IN IP4 10.0.0.1\r\n"
        "m=audio 10000 RTP/AVP 8 101\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
        "a=crypto:1 AES_256_CM_HMAC_SHA1_80 inline:abcd\r\n"
        "m=text 19266 RTP/AVP 100 98\r\n"
        "a=rtpmap:100 red/1000\r\n"
        "a=rtpmap:98 t140/1000\r\n"
    )

    OFFERLESS_REFRESH = "INVITE sip:x SIP/2.0\r\nVia: a\r\n\r\n"

    def test_rejects_crypto_less_offer_when_srtp_mandatory(self):
        from common import on_rx_reinvite
        prm = _FakePrm(self.OFFER_WITHOUT_CRYPTO)
        on_rx_reinvite(_FakeApp("mandatory"), prm)
        self.assertEqual(prm.statusCode, 488)

    def test_accepts_crypto_less_offer_when_srtp_optional(self):
        # при optional гвард не применяется: возможна легитимная
        # ренеготиация в plain RTP
        from common import on_rx_reinvite
        prm = _FakePrm(self.OFFER_WITHOUT_CRYPTO)
        on_rx_reinvite(_FakeApp("optional"), prm)
        self.assertEqual(prm.statusCode, 200)

    def test_accepts_offer_with_crypto(self):
        from common import on_rx_reinvite
        prm = _FakePrm(self.OFFER_WITH_CRYPTO)
        on_rx_reinvite(_FakeApp("mandatory"), prm)
        self.assertEqual(prm.statusCode, 200)

    def test_accepts_crypto_offer_with_text_media(self):
        """Renegotiation adding T.140/red text media must proceed normally:
        the audio validator ignores non-audio streams, so the guard must
        not interfere with an offer that carries proper SRTP crypto."""
        from common import on_rx_reinvite
        prm = _FakePrm(self.OFFER_WITH_CRYPTO_AND_TEXT)
        on_rx_reinvite(_FakeApp("mandatory"), prm)
        self.assertEqual(prm.statusCode, 200)

    def test_ignores_offerless_session_refresh(self):
        """A re-INVITE without SDP body is a session refresh — never reject."""
        from common import on_rx_reinvite
        prm = _FakePrm(self.OFFERLESS_REFRESH)
        on_rx_reinvite(_FakeApp("mandatory"), prm)
        self.assertEqual(prm.statusCode, 200)

    def test_ignores_reinvite_when_srtp_off(self):
        from common import on_rx_reinvite
        prm = _FakePrm(self.OFFER_WITHOUT_CRYPTO)
        on_rx_reinvite(_FakeApp("off"), prm)
        self.assertEqual(prm.statusCode, 200)


if __name__ == "__main__":
    unittest.main()
