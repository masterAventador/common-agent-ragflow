#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""内嵌媒体提取的单元测试。

覆盖 PDF 的三种内嵌方式与 OOXML 的 media 目录直存。Word/Excel 的 OLE 包装（M1b）不在此
文件覆盖范围内：本机没有可写 OLE 复合文件的手段，写不出失败测试就不实现。
"""

import io
import zipfile

import pytest

MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"MP4-PAYLOAD" * 8
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00" + b"MOV-PAYLOAD" * 8
MKV = b"\x1a\x45\xdf\xa3" + b"MKV-PAYLOAD" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"PNG-PAYLOAD" * 8


def _ooxml(parts: dict[str, bytes]) -> bytes:
    """按给定的包内路径造一个 OOXML(zip) 容器。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        for path, payload in parts.items():
            z.writestr(path, payload)
    return buf.getvalue()


def _pdf(objects: dict[int, bytes]) -> bytes:
    """按对象号拼一个带正确 xref 的最小 PDF。"""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_at)
    return bytes(out)


def _stream_obj(payload: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Type /EmbeddedFile %s /Length %d >>\nstream\n" % (extra, len(payload)) + payload + b"\nendstream"


def _pdf_with_names_embedded_file(payload: bytes, name: bytes) -> bytes:
    """把媒体挂在文档级 /Names /EmbeddedFiles 上（PDF 附件）。"""
    return _pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [ (%s) 4 0 R ] >> >> >>" % name,
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            4: b"<< /Type /Filespec /F (%s) /UF (%s) /EF << /F 5 0 R >> >>" % (name, name),
            5: _stream_obj(payload),
        }
    )


def _pdf_with_screen_annotation(payload: bytes, name: bytes) -> bytes:
    """把媒体挂在页面 /Screen 注释的 Rendition 动作上（Acrobat/WPS 插入视频的典型结构）。"""
    return _pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [4 0 R] >>",
            4: b"<< /Type /Annot /Subtype /Screen /Rect [10 10 190 120] /A 5 0 R >>",
            5: b"<< /Type /Action /S /Rendition /OP 0 /R 6 0 R /AN 4 0 R >>",
            6: b"<< /Type /Rendition /S /MR /C << /Type /MediaClip /S /MCD /D 7 0 R /CT (video/mp4) >> >>",
            7: b"<< /Type /Filespec /F (%s) /UF (%s) /EF << /F 8 0 R >> >>" % (name, name),
            8: _stream_obj(payload),
        }
    )


def _pdf_with_richmedia_annotation(payload: bytes, name: bytes) -> bytes:
    """把媒体挂在 /RichMedia 注释的 Assets 上。"""
    return _pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [4 0 R] >>",
            4: b"<< /Type /Annot /Subtype /RichMedia /Rect [10 10 190 120] /RichMediaContent 5 0 R >>",
            5: b"<< /Type /RichMediaContent /Assets << /Names [ (%s) 6 0 R ] >> >>" % name,
            6: b"<< /Type /Filespec /F (%s) /UF (%s) /EF << /F 7 0 R >> >>" % (name, name),
            7: _stream_obj(payload),
        }
    )


def _extract(binary: bytes):
    from rag.app.embedded_media import extract_embedded_media

    return extract_embedded_media(binary)


@pytest.mark.parametrize(
    "media_path, payload",
    [
        ("ppt/media/media1.mp4", MP4),
        ("ppt/media/media1.mov", MOV),
        ("word/media/media1.mp4", MP4),
        ("xl/media/media1.mp4", MP4),
    ],
)
def test_ooxml_media_directory_video_is_extracted(media_path, payload):
    """真实 WPS/PowerPoint 把视频原样存进 <part>/media/，当前上游只看 embeddings/ 因此全部漏掉。"""
    out = _extract(_ooxml({media_path: payload}))

    assert len(out) == 1
    name, data = out[0]
    assert name == media_path.rsplit("/", 1)[-1]
    assert data == payload


def test_ooxml_multiple_media_are_all_extracted():
    out = _extract(
        _ooxml(
            {
                "ppt/media/media1.mov": MOV,
                "ppt/media/media2.mp4": MP4,
                "ppt/media/image1.png": PNG,
            }
        )
    )

    assert sorted(name for name, _ in out) == ["media1.mov", "media2.mp4"]


def test_ooxml_images_and_xml_parts_are_not_returned():
    """图片已由既有解析器覆盖，本模块只负责音视频，不得把图片也交出去重复处理。"""
    out = _extract(_ooxml({"ppt/media/image1.png": PNG, "ppt/slides/slide1.xml": b"<sld/>"}))

    assert out == []


def test_pdf_names_embedded_file_video_is_extracted():
    out = _extract(_pdf_with_names_embedded_file(MP4, b"demo.mp4"))

    assert out == [("demo.mp4", MP4)]


def test_pdf_screen_annotation_video_is_extracted():
    out = _extract(_pdf_with_screen_annotation(MP4, b"lecture.mp4"))

    assert out == [("lecture.mp4", MP4)]


def test_pdf_richmedia_annotation_video_is_extracted():
    out = _extract(_pdf_with_richmedia_annotation(MOV, b"clip.mov"))

    assert out == [("clip.mov", MOV)]


def test_pdf_without_embedded_media_returns_empty():
    out = _extract(
        _pdf(
            {
                1: b"<< /Type /Catalog /Pages 2 0 R >>",
                2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            }
        )
    )

    assert out == []


@pytest.mark.parametrize("payload, expected_ext", [(MP4, ".mp4"), (MOV, ".mov"), (MKV, ".mkv")])
def test_video_extension_is_sniffed_when_name_has_none(payload, expected_ext):
    """容器里的条目未必带扩展名；下游按扩展名路由，嗅探不出就会被当成 .bin 丢弃。"""
    out = _extract(_ooxml({"ppt/media/media1": payload}))

    assert len(out) == 1
    assert out[0][0].endswith(expected_ext)
    assert out[0][1] == payload


def test_identical_media_is_returned_once():
    """同一段视频被多张幻灯片引用时只应解析一次，避免重复计费。"""
    out = _extract(_ooxml({"ppt/media/media1.mp4": MP4, "ppt/media/media2.mp4": MP4}))

    assert len(out) == 1


def test_non_container_input_returns_empty():
    assert _extract(b"plain text, not a container") == []
    assert _extract(b"") == []
