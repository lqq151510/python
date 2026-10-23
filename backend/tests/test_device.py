import pytest

from app.core import device

VM_STAT_SAMPLE = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                120000.
Pages active:                              800000.
Pages inactive:                            300000.
Pages speculative:                          20000.
Pages wired down:                          250000.
"""

SWAPUSAGE_SAMPLE = (
    "total = 13312.00M  used = 11912.50M  free = 1399.50M  (encrypted)"
)

MEMINFO_SAMPLE = """MemTotal:       32768000 kB
MemFree:         1024000 kB
MemAvailable:    8388608 kB
SwapTotal:       4194304 kB
SwapFree:        1048576 kB
HugePages_Total:       0
"""


def snapshot(
    *, available_gib: float, swap_used_gib: float, swap_total_gib: float = 13.0
) -> device.MemorySnapshot:
    return device.MemorySnapshot(
        total_bytes=32 * device.GIB,
        available_bytes=int(available_gib * device.GIB),
        swap_total_bytes=int(swap_total_gib * device.GIB),
        swap_used_bytes=int(swap_used_gib * device.GIB),
    )


def test_parse_vm_stat_reads_page_size_and_counts() -> None:
    page_size, counts = device.parse_vm_stat(VM_STAT_SAMPLE)
    assert page_size == 16384
    assert counts["Pages free"] == 120000
    assert counts["Pages speculative"] == 20000


def test_parse_vm_stat_defaults_page_size_without_header() -> None:
    page_size, counts = device.parse_vm_stat("Pages free: 2048.\n")
    assert page_size == 4096
    assert counts == {"Pages free": 2048}


def test_parse_vm_stat_rejects_unparsable_text() -> None:
    with pytest.raises(RuntimeError):
        device.parse_vm_stat("totally unrelated output")


def test_parse_swapusage_converts_megabytes() -> None:
    total, used = device.parse_swapusage(SWAPUSAGE_SAMPLE)
    assert total == int(13312.00 * 1024**2)
    assert used == int(11912.50 * 1024**2)


def test_parse_meminfo_converts_kibibytes() -> None:
    fields = device.parse_meminfo(MEMINFO_SAMPLE)
    assert fields["MemTotal"] == 32768000 * 1024
    assert fields["SwapTotal"] == 4194304 * 1024


def test_require_headroom_passes_with_budget_and_low_swap() -> None:
    result = device.require_headroom(
        "embedding_encode", reader=lambda: snapshot(available_gib=9.4, swap_used_gib=1.0)
    )
    assert result.available_bytes == int(9.4 * device.GIB)


def test_require_headroom_rejects_when_available_below_budget() -> None:
    with pytest.raises(device.DeviceMemoryError) as excinfo:
        device.require_headroom(
            "video_generation",
            reader=lambda: snapshot(available_gib=9.4, swap_used_gib=1.0),
        )
    message = str(excinfo.value)
    assert "视频生成" in message
    assert "20.0 GiB" in message
    assert "9.4 GiB" in message


def test_require_headroom_rejects_on_swap_pressure_even_with_headroom() -> None:
    with pytest.raises(device.DeviceMemoryError) as excinfo:
        device.require_headroom(
            "rag_stack",
            reader=lambda: snapshot(available_gib=30.0, swap_used_gib=12.9),
        )
    assert "swap" in str(excinfo.value)


@pytest.mark.parametrize("workload", ["unknown", "", "VIDEO_GENERATION"])
def test_require_headroom_rejects_unknown_workload(workload: str) -> None:
    with pytest.raises(ValueError):
        device.require_headroom(workload, reader=lambda: snapshot(available_gib=30.0, swap_used_gib=0.0))


def test_report_marks_workloads_against_current_memory() -> None:
    result = device.report(reader=lambda: snapshot(available_gib=9.4, swap_used_gib=11.6))
    workloads = result["workloads"]
    assert isinstance(workloads, dict)
    assert workloads["video_generation"]["fits"] is False
    assert workloads["embedding_finetune"]["fits"] is False
    assert workloads["embedding_encode"]["fits"] is True
    assert result["swap_pressure"] == pytest.approx(11.6 / 13.0, abs=1e-3)


def test_format_gib_is_one_decimal() -> None:
    assert device.format_gib(int(1.5 * device.GIB)) == "1.5 GiB"
