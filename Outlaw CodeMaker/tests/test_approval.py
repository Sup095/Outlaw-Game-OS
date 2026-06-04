"""Approval gate — blocking handshake, approve/deny, bypass cases."""
from PyQt6.QtCore import Qt

from core.approval import ApprovalGate


def test_disabled_gate_autoapproves(qapp):
    gate = ApprovalGate(enabled=False)
    assert gate.request("p.gd", "old", "new") is True


def test_no_actual_change_autoapproves(qapp):
    gate = ApprovalGate(enabled=True)
    assert gate.request("p.gd", "same", "same") is True


def test_direct_approve(qapp):
    gate = ApprovalGate(enabled=True)
    gate.review_requested.connect(
        lambda *a: gate.resolve(a[-1], True), Qt.ConnectionType.DirectConnection
    )
    assert gate.request("p.gd", "old", "new") is True


def test_direct_deny(qapp):
    gate = ApprovalGate(enabled=True)
    gate.review_requested.connect(
        lambda *a: gate.resolve(a[-1], False), Qt.ConnectionType.DirectConnection
    )
    assert gate.request("p.gd", "old", "new") is False


def test_review_payload_carries_kind(qapp):
    gate = ApprovalGate(enabled=True)
    seen = {}

    def on_review(path, old, new, kind, ticket):
        seen.update(path=path, kind=kind)
        gate.resolve(ticket, True)

    gate.review_requested.connect(on_review, Qt.ConnectionType.DirectConnection)
    gate.request("Main.tscn", "", "[gd_scene]", kind="tscn")
    assert seen == {"path": "Main.tscn", "kind": "tscn"}
