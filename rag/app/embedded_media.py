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
"""从 PDF 与 OOXML 容器中提取内嵌的音视频。

上游 `rag.utils.file_utils.extract_embed_file` 只覆盖 OOXML 的 embeddings/ 目录和 OLE 容器，
PDF 直接返回空，OOXML 的 media/ 目录也不在名单里——而真实 PowerPoint/WPS 正是把视频原样存进
`ppt/media/`。本模块补齐这些落点，只负责把媒体字节取出来，怎么切片、怎么限流由调用方决定。

只挖一层：容器里再套容器不递归展开，避免构造出来的嵌套文件放大解析成本。
"""

import hashlib
import io
import logging
import zipfile

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".flv", ".mpeg", ".mpg", ".webm", ".wmv", ".3gp", ".3gpp", ".mkv")
AUDIO_EXTS = (".wav", ".flac", ".ape", ".alac", ".wavpack", ".wv", ".mp3", ".aac", ".ogg", ".vorbis", ".opus")
MEDIA_EXTS = VIDEO_EXTS + AUDIO_EXTS

# 真实 PowerPoint / WPS 演示把视频原样写进 <part>/media/，Word 与 Excel 同构。
OOXML_MEDIA_DIRS = ("word/media/", "xl/media/", "ppt/media/")

_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_PDF_MAGIC = b"%PDF"


def _sha10(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:10]


def _sniff_media_ext(payload: bytes) -> str:
    """按 magic 判断媒体扩展名，判不出返回空串。

    容器里的条目未必带扩展名，而下游按扩展名决定走视频还是图片切片器，判不出就会被丢弃。
    """
    head = payload[:16]
    if len(head) < 12:
        return ""
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand[:2] == b"qt":
            return ".mov"
        if brand[:3] == b"M4A":
            return ".aac"
        return ".mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ".mkv"
    if head[:4] == b"RIFF":
        if head[8:12] == b"AVI ":
            return ".avi"
        if head[8:12] == b"WAVE":
            return ".wav"
        return ""
    if head[:3] == b"FLV":
        return ".flv"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:3] == b"ID3" or head[:2] == b"\xff\xfb":
        return ".mp3"
    return ""


def _media_name(raw_name: str, payload: bytes) -> str:
    """返回媒体文件名；不是音视频返回空串。"""
    base = raw_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if base.lower().endswith(MEDIA_EXTS):
        return base
    ext = _sniff_media_ext(payload)
    if not ext:
        return ""
    return f"{base}{ext}" if base else f"{_sha10(payload)}{ext}"


class _Collector:
    """按内容去重收集媒体，避免同一段视频被多处引用时重复解析。"""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.items: list[tuple[str, bytes]] = []

    def add(self, raw_name: str, payload: bytes) -> None:
        if not payload:
            return
        digest = _sha10(payload)
        if digest in self._seen:
            return
        name = _media_name(raw_name, payload)
        if not name:
            return
        self._seen.add(digest)
        self.items.append((name, payload))


def _collect_from_ooxml(binary: bytes, collector: _Collector) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(binary)) as archive:
            for entry in archive.namelist():
                if not entry.lower().startswith(OOXML_MEDIA_DIRS):
                    continue
                try:
                    collector.add(entry, archive.read(entry))
                except Exception:
                    logging.warning("[embedded_media] 无法读取 OOXML 条目 %s", entry)
    except Exception:
        logging.warning("[embedded_media] 无法打开 OOXML 容器")


def _filespec_name(node) -> str:
    for key in ("/UF", "/F"):
        value = node.get(key)
        if value:
            return str(value)
    return ""


def _collect_from_pdf(binary: bytes, collector: _Collector) -> None:
    """遍历 PDF 对象图收集所有 /EF 文件规格。

    /Names/EmbeddedFiles 附件、/Screen 注释挂的 Rendition、/RichMedia 的 Assets，三种嵌法最终
    都落到同一个结构：Filespec -> /EF -> 嵌入文件流。所以统一按 /EF 收，而不是分三套走法。
    用显式栈而不是递归，避免恶意构造的深层嵌套把解析进程打崩。
    """
    try:
        from pypdf import PdfReader
        from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    except Exception:
        logging.warning("[embedded_media] pypdf 不可用，跳过 PDF 内嵌媒体提取")
        return

    try:
        reader = PdfReader(io.BytesIO(binary))
    except Exception:
        logging.warning("[embedded_media] 无法解析 PDF")
        return

    visited: set = set()
    stack = [reader.trailer]
    while stack:
        node = stack.pop()
        if isinstance(node, IndirectObject):
            key = (node.idnum, node.generation)
            if key in visited:
                continue
            visited.add(key)
            try:
                node = node.get_object()
            except Exception:
                continue

        if isinstance(node, DictionaryObject):
            embedded = node.get("/EF")
            if embedded is not None:
                _collect_embedded_file(node, embedded, collector)
            stack.extend(node.values())
        elif isinstance(node, ArrayObject):
            stack.extend(node)


def _collect_embedded_file(filespec, embedded, collector: _Collector) -> None:
    try:
        embedded = embedded.get_object()
        stream = embedded.get("/F") or embedded.get("/UF")
        if stream is None:
            return
        payload = stream.get_object().get_data()
    except Exception:
        logging.warning("[embedded_media] 无法读取 PDF 内嵌文件流")
        return
    collector.add(_filespec_name(filespec), payload)


def extract_embedded_media(binary: bytes) -> list[tuple[str, bytes]]:
    """提取容器内嵌的音视频，返回 `(文件名, 字节)` 列表；不是容器或没有媒体时返回空列表。"""
    if not binary:
        return []

    collector = _Collector()
    head = bytes(binary[:8])
    if head.startswith(_ZIP_MAGIC):
        _collect_from_ooxml(bytes(binary), collector)
    elif head.startswith(_PDF_MAGIC):
        _collect_from_pdf(bytes(binary), collector)
    return collector.items
