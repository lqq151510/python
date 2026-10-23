"""AIGC 领域层单测：参数校验、工作流构造、ComfyUI 往返解析。

这些用例刻意不碰数据库和线程，用 httpx.MockTransport 假装 ComfyUI，
所以可以用 --noconftest 独立运行：
    cd backend && ../.venv/bin/python -m pytest tests/test_aigc.py --noconftest -q
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from app import aigc


def make_client(handler: httpx.MockTransport) -> aigc.ComfyClient:
    return aigc.ComfyClient(
        "http://127.0.0.1:8188",
        timeout=5,
        client=httpx.Client(transport=handler),
    )


# --- 参数 -------------------------------------------------------------------


def test_default_params_match_32gib_draft_preset():
    params = aigc.VideoParams()
    assert (params.width, params.height) == (480, 288)
    assert params.duration_seconds == 5.0
    assert params.memory_mode == "stream5"
    assert params.generation_profile == "Turbo 4 Fast"
    # 节点文档：480x288 约 4.3 分钟
    assert 250 <= params.estimate_seconds <= 262


@pytest.mark.parametrize("field", ["width", "height"])
def test_side_must_be_multiple_of_32(field):
    with pytest.raises(ValidationError) as excinfo:
        aigc.VideoParams(**{field: 470})
    assert "32 的倍数" in str(excinfo.value)


def test_unknown_param_rejected():
    with pytest.raises(ValidationError):
        aigc.VideoParams(fps=30)


def test_duration_is_rounded_to_one_decimal():
    assert aigc.VideoParams(duration_seconds=7.26).duration_seconds == 7.3


def test_params_json_roundtrip():
    params = aigc.VideoParams(width=640, height=352, duration_seconds=8.0, seed=7)
    restored = aigc.VideoParams.from_json(params.to_json())
    assert restored == params


def test_from_json_rejects_corrupt_payload():
    with pytest.raises(aigc.AigcError) as excinfo:
        aigc.VideoParams.from_json("{not json")
    assert excinfo.value.code == "aigc_params_corrupt"
    assert excinfo.value.status_code == 500


def test_from_json_rejects_non_object_payload():
    with pytest.raises(aigc.AigcError):
        aigc.VideoParams.from_json(json.dumps([1, 2, 3]))


def test_job_create_strips_prompt():
    job = aigc.AigcJobCreate(prompt="  霓虹城市夜景  ")
    assert job.prompt == "霓虹城市夜景"
    assert job.kind == aigc.KIND_TEXT2VIDEO


def test_job_create_rejects_blank_prompt():
    with pytest.raises(ValidationError):
        aigc.AigcJobCreate(prompt="   ")


# --- 工作流 -----------------------------------------------------------------


def test_build_prompt_text2video_graph():
    params = aigc.VideoParams(width=480, height=288, seed=123)
    graph = aigc.build_prompt(
        prompt="a neon city", params=params, filename_prefix="aigc/devassist_x"
    )
    assert sorted(graph) == ["1", "2", "3"]
    assert graph["1"]["class_type"] == "MiniMaxH3MLXTurbo"
    assert graph["1"]["inputs"]["width"] == 480
    assert graph["1"]["inputs"]["seed"] == 123
    assert "first_frame" not in graph["1"]["inputs"]
    assert graph["2"]["inputs"]["images"] == ["1", 0]
    assert graph["2"]["inputs"]["audio"] == ["1", 1]
    assert graph["3"]["inputs"]["video"] == ["2", 0]
    assert graph["3"]["inputs"]["format"] == "mp4"
    assert "audio" not in json.dumps(graph["3"])


def test_build_prompt_image2video_adds_loadimage():
    params = aigc.VideoParams()
    graph = aigc.build_prompt(
        prompt="animate this",
        params=params,
        filename_prefix="aigc/devassist_x",
        image_name="devassist/abc.png",
    )
    assert graph["4"]["class_type"] == "LoadImage"
    assert graph["4"]["inputs"]["image"] == "devassist/abc.png"
    assert graph["1"]["inputs"]["first_frame"] == ["4", 0]


def test_build_prompt_rejects_blank_prompt():
    params = aigc.VideoParams()
    with pytest.raises(aigc.AigcError) as excinfo:
        aigc.build_prompt(prompt="  ", params=params, filename_prefix="x")
    assert excinfo.value.code == "aigc_prompt_empty"


# --- 历史记录解析 -----------------------------------------------------------


def test_history_status_prefers_status_str():
    assert aigc.history_status({"status": {"status_str": "success"}}) == "success"
    assert aigc.history_status({"status": {"status_str": "error"}}) == "error"


def test_history_status_falls_back_to_completed_and_outputs():
    assert aigc.history_status({"status": {"completed": True}}) == "success"
    assert aigc.history_status({"outputs": {"3": {}}}) == "success"
    assert aigc.history_status({}) == "pending"


def test_history_error_reports_node_and_message():
    entry = {
        "status": {
            "messages": [
                [
                    "execution_error",
                    {"node_type": "MiniMaxH3MLXTurbo", "exception_message": "out of memory"},
                ]
            ]
        }
    }
    message = aigc.history_error(entry)
    assert "MiniMaxH3MLXTurbo" in message
    assert "out of memory" in message


def test_history_error_fallback_text():
    assert "未返回具体错误" in aigc.history_error({})


def test_saved_videos_filters_by_suffix():
    entry = {
        "outputs": {
            "3": {"images": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]},
            "4": {"images": [{"filename": "still.png", "subfolder": "", "type": "output"}]},
        }
    }
    videos = aigc.saved_videos(entry)
    assert [item["filename"] for item in videos] == ["clip.mp4"]


def test_estimate_progress_is_bounded():
    assert aigc.estimate_progress(0, 255.2) == 5
    assert aigc.estimate_progress(10_000, 255.2) == 95
    assert aigc.estimate_progress(5, 0) == 1


# --- ComfyClient ------------------------------------------------------------


def test_system_stats_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/system_stats"
        return httpx.Response(200, json={"system": {"comfyui_version": "0.41.0"}})

    with make_client(httpx.MockTransport(handler)) as client:
        assert client.system_stats()["system"]["comfyui_version"] == "0.41.0"


def test_system_stats_connect_error_maps_to_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(aigc.AigcError) as excinfo:
            client.system_stats()
    assert excinfo.value.code == "aigc_comfy_unreachable"
    assert excinfo.value.status_code == 503


def test_submit_returns_prompt_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"prompt_id": "pid-1", "number": 0})

    params = aigc.VideoParams(seed=1)
    graph = aigc.build_prompt(prompt="x", params=params, filename_prefix="p")
    with make_client(httpx.MockTransport(handler)) as client:
        assert client.submit(graph) == "pid-1"
    assert seen["path"] == "/prompt"
    assert seen["body"]["prompt"] == graph
    assert "client_id" in seen["body"]


def test_submit_rejection_maps_to_rejected():
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(400, text="prompt invalid")

    params = aigc.VideoParams(seed=1)
    graph = aigc.build_prompt(prompt="x", params=params, filename_prefix="p")
    with make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(aigc.AigcError) as excinfo:
            client.submit(graph)
    assert excinfo.value.code == "aigc_comfy_rejected"
    assert "prompt invalid" in excinfo.value.message


def test_submit_without_prompt_id_maps_to_rejected():
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"number": 1})

    params = aigc.VideoParams(seed=1)
    graph = aigc.build_prompt(prompt="x", params=params, filename_prefix="p")
    with make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(aigc.AigcError) as excinfo:
            client.submit(graph)
    assert excinfo.value.code == "aigc_comfy_rejected"


def test_upload_image_joins_subfolder():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/upload/image"
        return httpx.Response(200, json={"name": "abc.png", "subfolder": "devassist"})

    with make_client(httpx.MockTransport(handler)) as client:
        name = client.upload_image(
            filename="abc.png", content=b"png-bytes", content_type="image/png"
        )
    assert name == "devassist/abc.png"


def test_download_writes_file(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "clip.mp4"
        return httpx.Response(200, content=b"fake-mp4-bytes")

    target = tmp_path / "x" / "clip.mp4"
    with make_client(httpx.MockTransport(handler)) as client:
        written = client.download(
            filename="clip.mp4", subfolder="", folder_type="output", destination=target
        )
    assert written == len(b"fake-mp4-bytes")
    assert target.read_bytes() == b"fake-mp4-bytes"
    assert not target.with_suffix(".mp4.part").exists()


def test_download_missing_media(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(404, text="not found")

    with make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(aigc.AigcError) as excinfo:
            client.download(
                filename="clip.mp4",
                subfolder="",
                folder_type="output",
                destination=tmp_path / "clip.mp4",
            )
    assert excinfo.value.code == "aigc_output_missing"


def test_download_empty_media_raises_and_cleans_up(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, content=b"")

    target = tmp_path / "clip.mp4"
    with make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(aigc.AigcError) as excinfo:
            client.download(
                filename="clip.mp4", subfolder="", folder_type="output", destination=target
            )
    assert excinfo.value.code == "aigc_output_empty"
    assert not target.exists()


def test_output_filename_shape():
    name = aigc.output_filename("0123456789abcdef")
    assert name.startswith("01234567-")
    assert name.endswith(".mp4")
