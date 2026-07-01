# -*- coding: utf-8 -*-
"""
사진 설명 입력 도구 (PicSay) - v2
=============================================================
[v2 변경 사항]
- 윈도우 탐색기 "속성 > 자세히 > 설명" 필드가 실제로는 EXIF의 ImageDescription(270)이
  아니라 윈도우 전용 태그인 XPComment(40092)를 보여주는 경우가 있어서, v1에서는
  PicSay가 저장한 설명이 ImageDescription에만 들어가 탐색기 속성창에는 안 보이는
  문제가 있었음.
- v2부터는 jpg/tiff 저장 시 ImageDescription과 XPComment 두 태그에 동시에 기록하고,
  읽을 때도 XPComment를 우선 확인하도록 변경 (윈도우 탐색기와 100% 동일하게 보이도록).
- XPComment는 윈도우 규격상 UTF-16LE로 인코딩 + null 종료가 필요해서 별도 처리 추가.
==============================================================

하루에 찍은 여러 장의 사진을 한 화면에서 작은 썸네일 + 설명 입력란으로 죽 나열하고,
저장 버튼을 누르면 그 설명이 이미지 파일의 [속성 > 자세히 > 설명] 항목에 기록됨.

★ 외부 프로그램(exiftool 등) 설치가 전혀 필요 없음 ★
   - jpg/jpeg/tif/tiff : piexif 라이브러리로 EXIF ImageDescription + XPComment 태그에 직접 기록
   - png               : Pillow 로 PNG 텍스트 청크(Description)에 직접 기록 (픽셀 데이터는 무손실 그대로 유지)
   - webp              : Pillow + piexif 로 EXIF ImageDescription + XPComment 기록 (원본이 손실/무손실
                         압축인지 감지해서 동일하게 재저장)
   필요한 건 pip 라이브러리(piexif, Pillow)뿐이고, PyInstaller로 exe를 만들면 그 라이브러리들도
   exe 안에 그대로 포함되므로 배포 시 사용자는 exe 파일 하나만 있으면 됨.

사진 추가 방법 3가지:
  1) 리스트 영역에 파일을 드래그 앤 드롭
  2) "파일 선택" 버튼으로 여러 장 선택
  3) "폴더 선택" 버튼으로 폴더를 지정하면 그 안의 이미지 파일을 모두 자동으로 불러옴

설명 저장 방법 2가지:
  1) 각 사진 줄의 "저장" 버튼 -> 그 사진만 저장
  2) 상단의 "전체 저장" 버튼 -> 설명이 비어있지 않고 아직 저장 안 된(또는 수정된) 모든 항목을 일괄 저장

지원 형식: jpg, jpeg, png, tif, tiff, webp
  (bmp는 표준 메타데이터 저장 공간이 없어 지원하지 않음)
"""

import os
import sys

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QFileDialog, QFrame, QSizePolicy,
    QMessageBox, QToolButton, QSpacerItem
)

import piexif
from PIL import Image, PngImagePlugin

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
EXIF_EXTS = {".jpg", ".jpeg", ".tif", ".tiff"}   # piexif로 직접 처리
PNG_EXTS = {".png"}                               # PNG 텍스트 청크로 처리
WEBP_EXTS = {".webp"}                             # Pillow + piexif EXIF로 처리
THUMB_SIZE = 96


# ----------------------------------------------------------------------------
# 설명(ImageDescription) 읽기 / 쓰기 - 순수 파이썬 (피exif / Pillow)
# ----------------------------------------------------------------------------
def _detect_webp_lossless(filepath):
    """WEBP 파일의 RIFF 청크를 직접 검사해서 무손실(VP8L) 여부를 판단.
    Pillow가 다시 열 때는 img.info에 lossless 여부를 보존해주지 않으므로 파일을 직접 읽어 확인한다."""
    try:
        with open(filepath, "rb") as f:
            data = f.read()
        return b"VP8L" in data[:64] or b"VP8L" in data
    except Exception:
        return False


XP_COMMENT_TAG = 40092  # piexif.ImageIFD.XPComment


def _encode_xp_comment(text):
    """윈도우 전용 XPComment 태그 형식으로 인코딩: UTF-16LE + null 종료."""
    return text.encode("utf-16-le") + b"\x00\x00"


def _decode_xp_comment(raw):
    """XPComment 태그 값을 다시 문자열로 디코딩. piexif는 BYTE 배열을 정수 튜플로 주므로 bytes 변환 필요."""
    if raw is None:
        return ""
    try:
        if isinstance(raw, (tuple, list)):
            raw = bytes(raw)
        if not isinstance(raw, bytes):
            return ""
        return raw.rstrip(b"\x00").decode("utf-16-le", errors="ignore").strip()
    except Exception:
        return ""


def read_description(filepath):
    """기존 설명을 읽어옴. 실패하거나 없으면 빈 문자열."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext in EXIF_EXTS:
            exif_dict = piexif.load(filepath)
            # 윈도우 탐색기 "설명" 필드는 XPComment를 보여주는 경우가 많아 그것을 우선 확인
            xp_raw = exif_dict.get("0th", {}).get(XP_COMMENT_TAG)
            xp_text = _decode_xp_comment(xp_raw)
            if xp_text:
                return xp_text
            raw = exif_dict.get("0th", {}).get(piexif.ImageIFD.ImageDescription, b"")
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="ignore").rstrip("\x00").strip()
            return str(raw).strip()

        elif ext in PNG_EXTS:
            img = Image.open(filepath)
            text = getattr(img, "text", {}) or {}
            return text.get("Description", "") or text.get("Comment", "")

        elif ext in WEBP_EXTS:
            img = Image.open(filepath)
            exif_bytes = img.info.get("exif")
            if not exif_bytes:
                return ""
            exif_dict = piexif.load(exif_bytes)
            xp_raw = exif_dict.get("0th", {}).get(XP_COMMENT_TAG)
            xp_text = _decode_xp_comment(xp_raw)
            if xp_text:
                return xp_text
            raw = exif_dict.get("0th", {}).get(piexif.ImageIFD.ImageDescription, b"")
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="ignore").rstrip("\x00").strip()
            return str(raw).strip()

    except Exception:
        return ""

    return ""


def write_description(filepath, text):
    """설명을 파일에 기록. 성공시 (True, ""), 실패시 (False, 에러메시지)."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext in EXIF_EXTS:
            try:
                exif_dict = piexif.load(filepath)
            except Exception:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
            if "0th" not in exif_dict:
                exif_dict["0th"] = {}
            exif_dict["0th"][piexif.ImageIFD.ImageDescription] = text.encode("utf-8")
            exif_dict["0th"][XP_COMMENT_TAG] = _encode_xp_comment(text)
            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, filepath)
            return True, ""

        elif ext in PNG_EXTS:
            img = Image.open(filepath)
            img.load()  # 픽셀 데이터를 완전히 메모리로 읽어들임 (이후 같은 경로로 덮어써도 안전)
            meta = PngImagePlugin.PngInfo()
            # 기존 텍스트 청크들도 보존 (Description만 덮어씀)
            existing_text = getattr(img, "text", {}) or {}
            for key, value in existing_text.items():
                if key == "Description":
                    continue
                try:
                    meta.add_text(key, value)
                except Exception:
                    pass
            meta.add_text("Description", text)
            save_kwargs = {"pnginfo": meta}
            if "icc_profile" in img.info:
                save_kwargs["icc_profile"] = img.info["icc_profile"]
            img.save(filepath, "PNG", **save_kwargs)
            return True, ""

        elif ext in WEBP_EXTS:
            img = Image.open(filepath)
            img.load()
            is_lossless = _detect_webp_lossless(filepath)

            # 기존 EXIF 보존하면서 ImageDescription/XPComment 갱신
            exif_bytes_existing = img.info.get("exif")
            if exif_bytes_existing:
                try:
                    exif_dict = piexif.load(exif_bytes_existing)
                except Exception:
                    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
            else:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
            if "0th" not in exif_dict:
                exif_dict["0th"] = {}
            exif_dict["0th"][piexif.ImageIFD.ImageDescription] = text.encode("utf-8")
            exif_dict["0th"][XP_COMMENT_TAG] = _encode_xp_comment(text)
            exif_bytes = piexif.dump(exif_dict)

            save_kwargs = {"exif": exif_bytes}
            if is_lossless:
                save_kwargs["lossless"] = True
                save_kwargs["quality"] = 100
            else:
                # 원본 품질 정보가 없으면 고품질 기본값 사용 (완전한 원본 압축 파라미터 복원은 불가)
                save_kwargs["quality"] = img.info.get("quality", 90)
            img.save(filepath, "WEBP", **save_kwargs)
            return True, ""

        else:
            return False, f"지원하지 않는 형식입니다: {ext}"

    except Exception as e:
        return False, str(e)


# ----------------------------------------------------------------------------
# 백그라운드에서 저장 처리 (UI 멈춤 방지)
# ----------------------------------------------------------------------------
class SaveWorker(QThread):
    one_done = pyqtSignal(str, bool, str)   # filepath, success, error_msg
    all_done = pyqtSignal()

    def __init__(self, items):
        """items: list of (filepath, text)"""
        super().__init__()
        self.items = items

    def run(self):
        for filepath, text in self.items:
            ok, err = write_description(filepath, text)
            self.one_done.emit(filepath, ok, err)
        self.all_done.emit()


# ----------------------------------------------------------------------------
# 사진 한 줄 (썸네일 + 파일명 + 설명입력 + 상태 + 저장버튼)
# ----------------------------------------------------------------------------
class PhotoRow(QFrame):
    def __init__(self, filepath, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.saved_text = None  # 마지막으로 저장에 성공한 텍스트 (None = 저장 안됨)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("photoRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # 썸네일
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet(
            "background-color: #2b2b2b; border-radius: 6px;"
        )
        self._load_thumbnail()
        layout.addWidget(self.thumb_label)

        # 가운데: 파일명 + 설명입력
        mid_layout = QVBoxLayout()
        mid_layout.setSpacing(4)

        name_label = QLabel(os.path.basename(filepath))
        name_label.setStyleSheet("font-weight: 600; color: #ddd;")
        name_label.setToolTip(filepath)
        mid_layout.addWidget(name_label)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("이 사진에 대한 설명을 입력하세요...")
        existing = read_description(filepath)
        if existing:
            self.desc_edit.setText(existing)
            self.saved_text = existing
        self.desc_edit.textChanged.connect(self._on_text_changed)
        mid_layout.addWidget(self.desc_edit)

        layout.addLayout(mid_layout, stretch=1)

        # 상태 라벨
        self.status_label = QLabel("")
        self.status_label.setFixedWidth(70)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        self._update_status_label()

        # 개별 저장 버튼
        self.save_btn = QPushButton("저장")
        self.save_btn.setFixedWidth(64)
        self.save_btn.clicked.connect(self._on_save_clicked)
        layout.addWidget(self.save_btn)

        # 삭제(목록에서 제거) 버튼
        self.remove_btn = QToolButton()
        self.remove_btn.setText("✕")
        self.remove_btn.setToolTip("목록에서 제거 (파일은 삭제되지 않음)")
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.remove_btn)

        self._on_remove_callback = None

    def _load_thumbnail(self):
        pix = QPixmap(self.filepath)
        if pix.isNull():
            self.thumb_label.setText("미리보기\n없음")
            self.thumb_label.setStyleSheet(
                "background-color: #2b2b2b; border-radius: 6px; color: #888; font-size: 10px;"
            )
            return
        scaled = pix.scaled(
            THUMB_SIZE, THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.thumb_label.setPixmap(scaled)

    def _on_text_changed(self, _text):
        self._update_status_label()

    def is_dirty(self):
        """저장 필요한 변경이 있는지 (현재 텍스트가 마지막 저장 텍스트와 다름)"""
        return self.desc_edit.text() != (self.saved_text or "")

    def has_text(self):
        return bool(self.desc_edit.text().strip())

    def _update_status_label(self):
        if not self.has_text():
            self.status_label.setText("")
            self.status_label.setStyleSheet("color: #888;")
        elif self.is_dirty():
            self.status_label.setText("● 미저장")
            self.status_label.setStyleSheet("color: #e0a030;")
        else:
            self.status_label.setText("✓ 저장됨")
            self.status_label.setStyleSheet("color: #4caf50;")

    def mark_saved(self, text):
        self.saved_text = text
        self._update_status_label()

    def mark_save_failed(self):
        self.status_label.setText("⚠ 실패")
        self.status_label.setStyleSheet("color: #e05252;")

    def _on_save_clicked(self):
        text = self.desc_edit.text()
        self.save_btn.setEnabled(False)
        self.status_label.setText("저장중...")
        self.status_label.setStyleSheet("color: #888;")
        QApplication.processEvents()
        ok, err = write_description(self.filepath, text)
        self.save_btn.setEnabled(True)
        if ok:
            self.mark_saved(text)
        else:
            self.mark_save_failed()
            QMessageBox.warning(self, "저장 실패", f"{os.path.basename(self.filepath)}\n\n{err}")

    def set_remove_callback(self, cb):
        self._on_remove_callback = cb

    def _on_remove_clicked(self):
        if self._on_remove_callback:
            self._on_remove_callback(self)


# ----------------------------------------------------------------------------
# 메인 윈도우
# ----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("사진 설명 입력 도구 - PicSay")
        self.resize(820, 600)
        self.setAcceptDrops(True)

        self.rows = []  # PhotoRow 목록
        self.rows_by_path = {}  # 중복 추가 방지용

        self._build_ui()

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # 상단 툴바
        toolbar = QHBoxLayout()
        self.add_file_btn = QPushButton("파일 선택")
        self.add_file_btn.clicked.connect(self.on_add_files)
        toolbar.addWidget(self.add_file_btn)

        self.add_folder_btn = QPushButton("폴더 선택")
        self.add_folder_btn.clicked.connect(self.on_add_folder)
        toolbar.addWidget(self.add_folder_btn)

        toolbar.addItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding))

        self.save_all_btn = QPushButton("전체 저장")
        self.save_all_btn.setStyleSheet(
            "QPushButton { background-color: #3a7bd5; color: white; padding: 6px 16px; font-weight: 600; border-radius: 4px; }"
            "QPushButton:disabled { background-color: #555; color: #999; }"
        )
        self.save_all_btn.clicked.connect(self.on_save_all)
        toolbar.addWidget(self.save_all_btn)

        outer.addLayout(toolbar)

        # 안내 / 드롭 영역
        self.hint_label = QLabel("여기에 사진을 드래그 앤 드롭하세요  (또는 위의 버튼 사용)")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet(
            "color: #777; padding: 30px; border: 2px dashed #444; border-radius: 8px; font-size: 13px;"
        )
        outer.addWidget(self.hint_label)

        # 스크롤 영역 (사진 줄들)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setSpacing(6)
        self.scroll_layout.addStretch(1)
        self.scroll.setWidget(self.scroll_content)
        outer.addWidget(self.scroll, stretch=1)

        # 하단 상태표시
        self.bottom_label = QLabel("0장의 사진")
        self.bottom_label.setStyleSheet("color: #888; font-size: 11px;")
        outer.addWidget(self.bottom_label)

        # 다크 톤 배경
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QWidget { background-color: #1e1e1e; color: #ddd; }
            #photoRow { background-color: #262626; border-radius: 8px; border: 1px solid #333; }
            QLineEdit { background-color: #2f2f2f; border: 1px solid #444; border-radius: 4px; padding: 6px; color: #eee; }
            QPushButton { background-color: #3a3a3a; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px 10px; color: #eee; }
            QPushButton:hover { background-color: #454545; }
            QToolButton { background-color: transparent; border: none; color: #999; font-size: 14px; }
            QToolButton:hover { color: #e05252; }
            QScrollArea { border: none; }
        """)

    # ---------------- 드래그앤드롭 ----------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if not local_path:
                continue
            if os.path.isdir(local_path):
                paths.extend(self._collect_images_in_folder(local_path))
            elif self._is_image(local_path):
                paths.append(local_path)
        self._add_photos(paths)

    # ---------------- 파일/폴더 선택 ----------------
    def on_add_files(self):
        exts = " ".join(f"*{e}" for e in IMAGE_EXTS)
        files, _ = QFileDialog.getOpenFileNames(
            self, "사진 선택", "", f"이미지 파일 ({exts});;모든 파일 (*.*)"
        )
        if files:
            self._add_photos(files)

    def on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택")
        if folder:
            paths = self._collect_images_in_folder(folder)
            if not paths:
                QMessageBox.information(self, "알림", "선택한 폴더에 이미지 파일이 없습니다.")
                return
            self._add_photos(paths)

    @staticmethod
    def _is_image(path):
        return os.path.splitext(path)[1].lower() in IMAGE_EXTS

    @classmethod
    def _collect_images_in_folder(cls, folder):
        result = []
        try:
            for name in sorted(os.listdir(folder)):
                full = os.path.join(folder, name)
                if os.path.isfile(full) and cls._is_image(full):
                    result.append(full)
        except Exception:
            pass
        return result

    # ---------------- 사진 추가 ----------------
    def _add_photos(self, paths):
        added = 0
        for p in paths:
            p = os.path.normpath(p)
            if not self._is_image(p):
                continue
            if p in self.rows_by_path:
                continue  # 중복 방지
            row = PhotoRow(p)
            row.set_remove_callback(self._remove_row)
            # 마지막 stretch 앞에 삽입
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, row)
            self.rows.append(row)
            self.rows_by_path[p] = row
            added += 1

        if added:
            self.hint_label.setVisible(False)
        self._update_bottom_label()

    def _remove_row(self, row):
        self.scroll_layout.removeWidget(row)
        row.setParent(None)
        if row in self.rows:
            self.rows.remove(row)
        if row.filepath in self.rows_by_path:
            del self.rows_by_path[row.filepath]
        self._update_bottom_label()
        if not self.rows:
            self.hint_label.setVisible(True)

    def _update_bottom_label(self):
        total = len(self.rows)
        dirty = sum(1 for r in self.rows if r.has_text() and r.is_dirty())
        self.bottom_label.setText(f"{total}장의 사진  ·  미저장 {dirty}건")

    # ---------------- 전체 저장 ----------------
    def on_save_all(self):
        targets = [r for r in self.rows if r.has_text() and r.is_dirty()]
        if not targets:
            QMessageBox.information(self, "알림", "저장할 변경 사항이 없습니다.")
            return

        self.save_all_btn.setEnabled(False)
        self.save_all_btn.setText(f"저장 중... (0/{len(targets)})")
        for r in targets:
            r.status_label.setText("대기중")
            r.status_label.setStyleSheet("color: #888;")

        self._save_all_total = len(targets)
        self._save_all_done = 0

        items = [(r.filepath, r.desc_edit.text()) for r in targets]
        self.worker = SaveWorker(items)
        self.worker.one_done.connect(self._on_one_saved)
        self.worker.all_done.connect(self._on_all_saved)
        self.worker.start()

    def _on_one_saved(self, filepath, ok, err):
        self._save_all_done += 1
        self.save_all_btn.setText(f"저장 중... ({self._save_all_done}/{self._save_all_total})")
        row = self.rows_by_path.get(filepath)
        if row:
            if ok:
                row.mark_saved(row.desc_edit.text())
            else:
                row.mark_save_failed()
        self._update_bottom_label()

    def _on_all_saved(self):
        self.save_all_btn.setEnabled(True)
        self.save_all_btn.setText("전체 저장")
        self._update_bottom_label()
        QMessageBox.information(self, "완료", f"{self._save_all_total}건 저장을 완료했습니다.")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PicSay")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()