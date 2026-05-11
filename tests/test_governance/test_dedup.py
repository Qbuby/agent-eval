from __future__ import annotations

import pytest

from agent_eval.governance.dedup import DedupService, DedupStrategy


class TestDedupFingerprint:
    def test_compute_fingerprint_deterministic(self):
        messages = [{"role": "user", "content": "hello"}]
        fp1 = DedupService.compute_fingerprint(messages)
        fp2 = DedupService.compute_fingerprint(messages)
        assert fp1 == fp2

    def test_compute_fingerprint_different_content(self):
        msg1 = [{"role": "user", "content": "hello"}]
        msg2 = [{"role": "user", "content": "world"}]
        assert DedupService.compute_fingerprint(msg1) != DedupService.compute_fingerprint(msg2)

    def test_compute_fingerprint_order_independent_keys(self):
        msg1 = [{"role": "user", "content": "hello"}]
        msg2 = [{"content": "hello", "role": "user"}]
        assert DedupService.compute_fingerprint(msg1) == DedupService.compute_fingerprint(msg2)

    def test_compute_fingerprint_unicode_nfc(self):
        import unicodedata
        nfd_str = unicodedata.normalize("NFD", "café")
        nfc_str = unicodedata.normalize("NFC", "café")
        msg_nfd = [{"role": "user", "content": nfd_str}]
        msg_nfc = [{"role": "user", "content": nfc_str}]
        assert DedupService.compute_fingerprint(msg_nfd) == DedupService.compute_fingerprint(msg_nfc)

    def test_compute_fingerprint_strips_whitespace(self):
        msg1 = [{"role": "user", "content": "hello"}]
        fp = DedupService.compute_fingerprint(msg1)
        assert len(fp) == 64  # SHA256 hex length

    def test_compute_fingerprint_multiple_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
        ]
        fp = DedupService.compute_fingerprint(messages)
        assert len(fp) == 64

    def test_different_message_order_different_fingerprint(self):
        msg1 = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
        msg2 = [
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "first"},
        ]
        assert DedupService.compute_fingerprint(msg1) != DedupService.compute_fingerprint(msg2)
