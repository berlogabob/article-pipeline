import socket

import pytest
import requests

from article_pipeline import images


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        headers=None,
        chunks=None,
        raise_mid_stream=False,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [b"fake image bytes"]
        self._raise_mid_stream = raise_mid_stream
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i, chunk in enumerate(self._chunks):
            if self._raise_mid_stream and i == 1:
                raise requests.exceptions.ChunkedEncodingError("stream broke")
            yield chunk

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    """Keep _is_safe_public_http_url's DNS check offline and deterministic.

    A real getaddrinfo() call would either hit the network or, if offline,
    already raise gaierror (which the resolver treats as "not private").
    Patching it makes that behavior explicit and network-independent.
    """

    def fake_getaddrinfo(host, *args, **kwargs):
        raise socket.gaierror("offline test: no DNS resolution")

    monkeypatch.setattr("article_pipeline.net.socket.getaddrinfo", fake_getaddrinfo)


def test_download_images_happy_path_writes_1_jpg(tmp_path, monkeypatch):
    resp = FakeResponse(
        status_code=200,
        headers={"Content-Type": "image/jpeg"},
        chunks=[b"abc", b"def"],
    )
    monkeypatch.setattr(images.requests, "get", lambda *a, **kw: resp)

    dest = tmp_path / "assets"
    result = images.download_images(["https://good.example/pic.jpg"], dest)

    assert result == [dest / "1.jpg"]
    assert (dest / "1.jpg").read_bytes() == b"abcdef"
    assert resp.closed is True


def test_download_images_404_is_skipped(tmp_path, monkeypatch):
    resp = FakeResponse(status_code=404)
    monkeypatch.setattr(images.requests, "get", lambda *a, **kw: resp)

    dest = tmp_path / "assets"
    result = images.download_images(["https://good.example/missing.jpg"], dest)

    assert result == []
    assert not dest.exists()


def test_download_images_oversized_content_length_is_skipped(tmp_path, monkeypatch):
    resp = FakeResponse(
        status_code=200,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(10_000_000)},
    )
    monkeypatch.setattr(images.requests, "get", lambda *a, **kw: resp)

    dest = tmp_path / "assets"
    result = images.download_images(
        ["https://good.example/big.jpg"], dest, max_bytes=8_388_608
    )

    assert result == []
    assert not dest.exists()


def test_download_images_oversized_streamed_bytes_is_skipped(tmp_path, monkeypatch):
    resp = FakeResponse(
        status_code=200,
        headers={"Content-Type": "image/jpeg"},
        chunks=[b"x" * 10, b"y" * 10],
    )
    monkeypatch.setattr(images.requests, "get", lambda *a, **kw: resp)

    dest = tmp_path / "assets"
    result = images.download_images(["https://good.example/big.jpg"], dest, max_bytes=15)

    assert result == []
    assert not dest.exists()


def test_download_images_private_ip_url_is_skipped_without_request(tmp_path, monkeypatch):
    def fake_get(*args, **kwargs):
        raise AssertionError("requests.get must not be called for unsafe URLs")

    monkeypatch.setattr(images.requests, "get", fake_get)

    dest = tmp_path / "assets"
    result = images.download_images(["http://127.0.0.1/secret.jpg"], dest)

    assert result == []
    assert not dest.exists()


def test_download_images_broken_stream_mid_way_skips_others_still_saved(
    tmp_path, monkeypatch
):
    good1 = FakeResponse(
        status_code=200, headers={"Content-Type": "image/jpeg"}, chunks=[b"one"]
    )
    broken = FakeResponse(
        status_code=200,
        headers={"Content-Type": "image/png"},
        chunks=[b"start", b"boom"],
        raise_mid_stream=True,
    )
    good2 = FakeResponse(
        status_code=200, headers={"Content-Type": "image/webp"}, chunks=[b"two"]
    )

    responses = iter([good1, broken, good2])
    monkeypatch.setattr(images.requests, "get", lambda *a, **kw: next(responses))

    dest = tmp_path / "assets"
    result = images.download_images(
        [
            "https://good.example/1.jpg",
            "https://good.example/2.png",
            "https://good.example/3.webp",
        ],
        dest,
    )

    assert [p.name for p in result] == ["1.jpg", "3.webp"]
    assert (dest / "1.jpg").read_bytes() == b"one"
    assert (dest / "3.webp").read_bytes() == b"two"
    assert not (dest / "2.png").exists()


def test_download_images_respects_max_images(tmp_path, monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse(status_code=200, headers={"Content-Type": "image/jpeg"})

    monkeypatch.setattr(images.requests, "get", fake_get)

    dest = tmp_path / "assets"
    urls = [f"https://good.example/{i}.jpg" for i in range(5)]
    result = images.download_images(urls, dest, max_images=2)

    assert len(result) == 2


def test_download_images_no_dir_created_when_all_fail(tmp_path, monkeypatch):
    resp = FakeResponse(status_code=404)
    monkeypatch.setattr(images.requests, "get", lambda *a, **kw: resp)

    dest = tmp_path / "assets"
    result = images.download_images(["https://good.example/missing.jpg"], dest)

    assert result == []
    assert not dest.exists()
