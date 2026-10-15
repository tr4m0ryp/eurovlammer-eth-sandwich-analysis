"""The exec risk-recognition guardrails flag balance-lock / dump / slippage vectors and pass clean tokens."""
import struct

from tlive.exec.risk import event_risks, gate
from tlive.exec.safety import freeze_authority, mint_authority, risky_extensions


def _mint(freeze: bool = False, mint_auth: bool = False) -> bytes:
    data = bytearray(82)
    struct.pack_into("<Q", data, 36, 1_000_000_000_000_000)   # supply
    data[44] = 6                                               # decimals
    data[45] = 1                                               # is_initialized
    if mint_auth:
        struct.pack_into("<I", data, 0, 1)
    if freeze:
        struct.pack_into("<I", data, 46, 1)
    return bytes(data)


def test_authority_checks():
    assert mint_authority(_mint(mint_auth=True)) is True
    assert mint_authority(_mint()) is False
    assert freeze_authority(_mint(freeze=True)) is True
    assert freeze_authority(_mint()) is False


def test_risky_extensions():
    assert risky_extensions(_mint()) == {"transfer_hook": False, "non_transferable": False, "transfer_fee": False}
    d = bytearray(_mint()) + bytearray(4 + 72)
    struct.pack_into("<HH", d, 82, 14, 72)                    # transfer hook
    assert risky_extensions(bytes(d))["transfer_hook"] is True
    d = bytearray(_mint()) + bytearray(4)
    struct.pack_into("<HH", d, 82, 9, 0)                      # non-transferable
    assert risky_extensions(bytes(d))["non_transferable"] is True
    d = bytearray(_mint()) + bytearray(4 + 108)
    struct.pack_into("<HH", d, 82, 1, 108)                    # transfer fee
    assert risky_extensions(bytes(d))["transfer_fee"] is True


def _row(**kw):
    r = {"front_budget": 5_000_000, "x_ref": 100_000_000_000, "p": 0.5, "cu_price": 10_000}
    r.update(kw)
    return r


def test_gate_flags_slippage_confidence_and_unsafe_token():
    assert gate({"safe": True}, event_risks(_row())) == (True, [])
    ok, reasons = gate({"safe": True}, event_risks(_row(x_ref=50_000_000)))   # front = 10% of pool
    assert not ok and any("slippage" in r for r in reasons)
    ok, reasons = gate({"safe": True}, event_risks(_row(p=0.001)))
    assert not ok and any("confidence" in r for r in reasons)
    ok, reasons = gate({"safe": False, "reason": "freeze_authority"}, event_risks(_row()))
    assert not ok and "freeze_authority" in reasons
