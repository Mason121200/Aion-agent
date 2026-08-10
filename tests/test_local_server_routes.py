"""??? local_server????????????????

?? APK ??? API?/api/upload?/uploads/*???????????????
?? 404/400 ??????????????????? LLM?
"""
import http.client
import json
import threading

import pytest

from aion_agent.server import local_server as ls
from aion_agent.server.runtime import AppRuntime


def _tiny_png():
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd400000000"
        "49454e44ae426082"
    )


def _multipart(body: bytes, filename: str, content_type: str) -> bytes:
    boundary = "----aiontestboundary"
    head = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="file"; filename="%s"\r\n'
        "Content-Type: %s\r\n"
        "\r\n"
    ) % (boundary, filename, content_type)
    return head.encode("utf-8") + body + ("\r\n--%s--\r\n" % boundary).encode("utf-8")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    rt = AppRuntime(data_dir=tmp_path / "data")
    monkeypatch.setattr(ls, "_runtime", rt)
    httpd = ls.ThreadingHTTPServer(("127.0.0.1", 0), ls.LocalHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield port, rt
    httpd.shutdown()
    httpd.server_close()


def _request(method, port, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type"), data


def _get(port, path):
    return _request("GET", port, path)


def _post_json(port, path, payload):
    return _request(
        "POST", port, path,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def test_upload_roundtrip_and_static(server):
    port, _ = server
    body = _multipart(_tiny_png(), "a.png", "image/png")
    status, _, data = _request(
        "POST", port, "/api/upload", body=body,
        headers={"Content-Type": "multipart/form-data; boundary=----aiontestboundary"},
    )
    assert status == 200, data
    url = json.loads(data)["url"]
    assert url.startswith("/uploads/up_") and url.endswith(".png")
    status, ctype, img = _get(port, url)
    assert status == 200
    assert ctype == "image/png"
    assert img == _tiny_png()


def test_upload_rejects_non_image(server):
    port, _ = server
    body = _multipart(b"hello", "a.txt", "text/plain")
    status, _, data = _request(
        "POST", port, "/api/upload", body=body,
        headers={"Content-Type": "multipart/form-data; boundary=----aiontestboundary"},
    )
    assert status == 400, data


def test_sync_status_skills_tools(server):
    port, _ = server
    status, _, data = _get(port, "/api/sync/status")
    assert status == 200, data
    payload = json.loads(data)
    assert "device_id" in payload and "files" in payload

    status, _, data = _get(port, "/api/skills")
    assert status == 200, data
    skills = json.loads(data)["skills"]
    assert isinstance(skills, list) and skills
    assert "enabled" in skills[0]

    status, _, data = _get(port, "/api/tools")
    assert status == 200, data
    tools = json.loads(data)
    assert "tools" in tools and "policy" in tools


def test_skill_toggle_known_and_unknown(server):
    port, _ = server
    _, _, data = _get(port, "/api/skills")
    name = json.loads(data)["skills"][0]["name"]
    status, _, data = _post_json(port, "/api/skills/%s/toggle" % name, {"enabled": False})
    assert status == 200, data
    status, _, data = _post_json(port, "/api/skills/not_a_skill/toggle", {"enabled": True})
    assert status == 404, data


def test_study_routes_missing_plan_and_bad_params(server):
    port, _ = server
    status, _, data = _get(port, "/api/study/plans/nope")
    assert status == 404, data
    status, _, data = _get(port, "/api/study/plans/nope/analysis")
    assert status == 404, data
    status, _, data = _post_json(port, "/api/study/tests", {})
    assert status == 400, data
    status, _, data = _post_json(port, "/api/study/mistakes", {})
    assert status == 400, data


def test_unknown_api_404(server):
    port, _ = server
    status, _, data = _get(port, "/api/does_not_exist")
    assert status == 404, data
