from types import SimpleNamespace

from torrent_clients.utils.labels import apply_labels_by_rule, compute_labels


def test_compute_labels_replaces_prefix_and_adds_labels() -> None:
    result = compute_labels(
        current=["源：Old", "keep"],
        replace_prefix={"源：": "源：CHD"},
        add=["新增"],
        remove=["to-remove"],
    )

    assert result == ["keep", "源：CHD", "新增"]


def test_compute_labels_removes_exact_labels_and_deduplicates() -> None:
    result = compute_labels(
        current=["A", "B", "A", "C"],
        remove=["B"],
        add=["C", "D"],
    )

    assert result == ["A", "C", "D"]


def test_apply_labels_by_rule_only_writes_when_labels_changed() -> None:
    calls = []

    class _Client:
        def set_labels(self, torrent, labels):
            calls.append((torrent.hash_string, labels))

    torrent = SimpleNamespace(hash_string="hash-1", labels=["源：Old", "keep"])
    changed = apply_labels_by_rule(
        client=_Client(),
        torrent=torrent,
        replace_prefix={"源：": "源：CHD"},
    )

    assert changed is True
    assert torrent.labels == ["keep", "源：CHD"]
    assert calls == [("hash-1", ["keep", "源：CHD"])]

    calls.clear()
    changed = apply_labels_by_rule(
        client=_Client(),
        torrent=torrent,
        replace_prefix={"源：": "源：CHD"},
    )

    assert changed is False
    assert calls == []
