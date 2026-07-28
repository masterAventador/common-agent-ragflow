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


# --- M2：把提取到的媒体切片并挂到父文档上 -------------------------------------


def _fake_media_chunker(answer_by_name: dict[str, str], failing: tuple[str, ...] = ()):
    """替身切片器，签名与 rag.app.picture.chunk 一致。

    真实切片器会调用百炼视频理解，且 rag.app.picture 模块级就构造 OCR() 触发模型下载，
    单元测试既不能联网也不该等下载，因此这里注入替身；真实链路由 M7 验收。
    """

    def _chunker(filename, binary, tenant_id, lang, callback=None, **kwargs):
        if filename in failing:
            raise RuntimeError("模型调用失败")
        return [
            {
                "docnm_kwd": filename,
                "title_tks": filename,
                "doc_type_kwd": "video",
                "content_with_weight": answer_by_name[filename],
                "content_ltks": answer_by_name[filename],
            }
        ]

    return _chunker


def _chunk_media(binary, parent_name="季度汇报.pptx", enabled=True, chunker=None, callback=None):
    from rag.app.embedded_media import EMBEDDED_MEDIA_ENABLED_KEY, chunk_embedded_media

    return chunk_embedded_media(
        parent_name,
        binary,
        tenant_id="t-1",
        lang="Chinese",
        callback=callback,
        parser_config={EMBEDDED_MEDIA_ENABLED_KEY: True} if enabled else {},
        media_chunker=chunker,
    )


def test_embedded_media_parsing_is_disabled_by_default():
    """整段视频要送模型，成本不可忽略，未显式开启不得触发。"""
    out = _chunk_media(_ooxml({"ppt/media/media1.mp4": MP4}), enabled=False)

    assert out == []


def test_each_embedded_video_produces_one_chunk():
    binary = _ooxml({"ppt/media/media1.mp4": MP4, "ppt/media/media2.mov": MOV})
    chunker = _fake_media_chunker({"media1.mp4": "讲解了季度目标", "media2.mov": "演示了操作流程"})

    out = _chunk_media(binary, chunker=chunker)

    assert len(out) == 2


def test_chunk_is_attributed_to_the_parent_document():
    """引用里必须显示父文档名，不能冒出 media1.mp4 这种用户没上传过的文件名。"""
    chunker = _fake_media_chunker({"media1.mp4": "讲解了季度目标"})

    out = _chunk_media(_ooxml({"ppt/media/media1.mp4": MP4}), parent_name="季度汇报.pptx", chunker=chunker)

    assert out[0]["docnm_kwd"] == "季度汇报.pptx"


def test_chunk_content_marks_which_embedded_media_it_came_from():
    chunker = _fake_media_chunker({"media1.mp4": "讲解了季度目标"})

    out = _chunk_media(_ooxml({"ppt/media/media1.mp4": MP4}), chunker=chunker)

    assert out[0]["content_with_weight"].startswith("【内嵌视频 media1.mp4】")
    assert "讲解了季度目标" in out[0]["content_with_weight"]


def test_chunk_tokens_stay_consistent_with_marked_content():
    """只改 content_with_weight 而不重算分词，检索命中的正文与展示的正文会不一致。

    不断言具体切分结果——分词器怎么切 `media1.mp4` 是它的实现细节；这里只要求标记和原始
    回答都进入了分词字段。
    """
    chunker = _fake_media_chunker({"media1.mp4": "讲解了季度目标"})

    out = _chunk_media(_ooxml({"ppt/media/media1.mp4": MP4}), chunker=chunker)

    tokens = out[0]["content_ltks"]
    assert "media1" in tokens
    assert "季度" in tokens


def test_chunk_keeps_video_doc_type_for_downstream_marking():
    chunker = _fake_media_chunker({"media1.mp4": "讲解了季度目标"})

    out = _chunk_media(_ooxml({"ppt/media/media1.mp4": MP4}), chunker=chunker)

    assert out[0]["doc_type_kwd"] == "video"


def test_one_media_failure_does_not_drop_the_others():
    binary = _ooxml({"ppt/media/media1.mp4": MP4, "ppt/media/media2.mov": MOV})
    chunker = _fake_media_chunker({"media2.mov": "演示了操作流程"}, failing=("media1.mp4",))

    out = _chunk_media(binary, chunker=chunker)

    assert len(out) == 1
    assert "演示了操作流程" in out[0]["content_with_weight"]


def test_media_failure_is_reported_instead_of_silently_dropped():
    """当前上游对内嵌文件失败只 log 不提示，用户看到解析成功却少了内容。"""
    messages = []
    chunker = _fake_media_chunker({}, failing=("media1.mp4",))

    def _record(*args, **kwargs):
        messages.append(" ".join(str(value) for value in (*args, *kwargs.values())))

    _chunk_media(_ooxml({"ppt/media/media1.mp4": MP4}), chunker=chunker, callback=_record)

    assert any("media1.mp4" in message for message in messages)


def test_container_without_media_produces_no_chunk():
    assert _chunk_media(_ooxml({"ppt/media/image1.png": PNG})) == []
    assert _chunk_media(b"plain text") == []


# --- M2：与主文档 chunk 列表的合并（task_executor hook 的全部逻辑） -------------


def _append(chunks, binary, enabled=True, chunker=None):
    from rag.app.embedded_media import EMBEDDED_MEDIA_ENABLED_KEY, append_embedded_media_chunks

    return append_embedded_media_chunks(
        chunks,
        "季度汇报.pptx",
        binary,
        tenant_id="t-1",
        lang="Chinese",
        callback=None,
        parser_config={EMBEDDED_MEDIA_ENABLED_KEY: True} if enabled else {},
        media_chunker=chunker,
    )


def test_embedded_chunks_are_appended_after_the_main_document_chunks():
    """必须追加到尾部：task_executor 用 cks[0] 取 PDF 大纲，插到前面会把大纲丢掉。"""
    main = [{"content_with_weight": "正文一", "__outline__": [("章节", 1)]}, {"content_with_weight": "正文二"}]
    chunker = _fake_media_chunker({"media1.mp4": "讲解了季度目标"})

    out = _append(main, _ooxml({"ppt/media/media1.mp4": MP4}), chunker=chunker)

    assert len(out) == 3
    assert out[0]["__outline__"] == [("章节", 1)]
    assert out[1]["content_with_weight"] == "正文二"
    assert out[2]["content_with_weight"].startswith("【内嵌视频 media1.mp4】")


def test_main_chunks_are_returned_untouched_when_disabled():
    main = [{"content_with_weight": "正文一"}]

    out = _append(main, _ooxml({"ppt/media/media1.mp4": MP4}), enabled=False)

    assert out == main


def test_main_chunks_are_returned_untouched_without_embedded_media():
    main = [{"content_with_weight": "正文一"}]

    assert _append(main, _ooxml({"ppt/media/image1.png": PNG})) == main


def test_empty_main_chunks_still_receive_embedded_media():
    """PDF 正文为空但内嵌了视频时，不能因为主列表为空就整份丢弃。"""
    chunker = _fake_media_chunker({"media1.mp4": "讲解了季度目标"})

    out = _append([], _ooxml({"ppt/media/media1.mp4": MP4}), chunker=chunker)

    assert len(out) == 1
