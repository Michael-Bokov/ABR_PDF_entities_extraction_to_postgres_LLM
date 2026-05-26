from __future__ import annotations

import logging
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

# Порядок для ZIP_FILENAME_ENCODING=auto
_ENCODING_TRY_ORDER = ("cp866", "cp1251", "utf-8", "latin-1")


def _cyrillic_score(text: str) -> int:
    return sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")


def _has_cyrillic(text: str) -> bool:
    return _cyrillic_score(text) > 0


def fix_mojibake_unicode(name: str) -> str:
    """
    Исправляет уже испорченные Unicode-имена на диске.

    Типичный случай: CP866/CP1251 прочитали как UTF-8 → «âÄæÆ».
    """
    if not name or _has_cyrillic(name):
        return name

    best = name
    best_score = _cyrillic_score(name)

    attempts: list[tuple[str, str]] = [
        ("latin-1", "cp1251"),
        ("latin-1", "cp866"),
        ("cp1252", "cp1251"),
        ("cp1252", "cp866"),
        ("utf-8", "cp1251"),
        ("utf-8", "cp866"),
        ("cp437", "cp866"),
        ("cp437", "cp1251"),
    ]
    for enc_from, enc_to in attempts:
        try:
            fixed = name.encode(enc_from).decode(enc_to)
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        score = _cyrillic_score(fixed)
        if score > best_score:
            best_score = score
            best = fixed

    return best


def decode_zip_filename(name: str, encoding: str) -> str:
    """Декодирование имени из ZIP (сырое имя прочитано как cp437)."""
    if not name:
        return name
    if _has_cyrillic(name):
        return name

    encodings = [encoding] if encoding != "auto" else list(_ENCODING_TRY_ORDER)
    seen: set[str] = set()
    best = name
    best_score = 0

    for enc in encodings:
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            fixed = name.encode("cp437").decode(enc)
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        score = _cyrillic_score(fixed)
        if score > best_score:
            best_score = score
            best = fixed

    return fix_mojibake_unicode(best)


def normalize_path_encoding(path: Path) -> str:
    """Исправление кракозябр в имени файла/папки на диске (при регистрации в БД)."""
    parts = [fix_mojibake_unicode(p) for p in path.parts]
    return str(Path(*parts))


def _member_target_name(info: zipfile.ZipInfo, encoding: str) -> str:
    # UTF-8 флаг в ZIP — доверяем только если реально есть кириллица
    if info.flag_bits & 0x800:
        name = info.filename
        if _has_cyrillic(name):
            return name
        # Ложный UTF-8 флаг (часто у WinRAR/7-Zip) — перекодируем
        return decode_zip_filename(name, encoding)
    return decode_zip_filename(info.filename, encoding)


def _safe_target_path(target_dir: Path, member_name: str) -> Path:
    rel = PurePosixPath(member_name.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Небезопасный путь в ZIP: {member_name}")
    dest = target_dir.joinpath(*rel.parts)
    resolved = dest.resolve()
    if not str(resolved).startswith(str(target_dir.resolve())):
        raise ValueError(f"Небезопасный путь в ZIP: {member_name}")
    return dest


def extract_zip_with_encoding(
    zip_path: Path,
    target_dir: Path,
    encoding: str,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)

    # Всегда читаем метаданные как cp437, затем сами декодируем.
    # metadata_encoding=utf-8 из env ЛОМАЕТ русские имена — не используем.
    meta_enc = "cp437" if sys.version_info >= (3, 11) else None

    if meta_enc:
        with zipfile.ZipFile(zip_path, "r", metadata_encoding=meta_enc) as zf:
            _extract_members(zf, target_dir, encoding)
    else:
        with zipfile.ZipFile(zip_path, "r") as zf:
            _extract_members(zf, target_dir, encoding)


def _extract_members(
    zf: zipfile.ZipFile,
    target_dir: Path,
    encoding: str,
) -> None:
    for info in zf.infolist():
        name = _member_target_name(info, encoding)
        dest = _safe_target_path(target_dir, name)
        if name.endswith("/") or info.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def extract_zip_archives(
    input_dir: Path,
    *,
    filename_encoding: str = "cp866",
    force_reextract: bool = False,
) -> list[Path]:
    extracted: list[Path] = []
    for zip_path in sorted(input_dir.glob("*.zip")):
        target_dir = input_dir / zip_path.stem
        done_marker = zip_path.with_suffix(".zip.done")

        if (
            not force_reextract
            and target_dir.exists()
            and any(target_dir.rglob("*.pdf"))
        ):
            logger.info("ZIP уже распакован: %s", zip_path.name)
            extracted.extend(target_dir.rglob("*.pdf"))
            continue

        if force_reextract and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            done_marker.unlink(missing_ok=True)

        try:
            extract_zip_with_encoding(zip_path, target_dir, filename_encoding)
            logger.info(
                "Распакован: %s -> %s (ZIP_FILENAME_ENCODING=%s)",
                zip_path.name,
                target_dir,
                filename_encoding,
            )
            extracted.extend(target_dir.rglob("*.pdf"))
            done_marker.write_text(f"encoding={filename_encoding}", encoding="utf-8")
        except zipfile.BadZipFile as exc:
            logger.error("Повреждённый ZIP %s: %s", zip_path, exc)
    return extracted


def discover_pdfs(
    input_dir: Path,
    *,
    filename_encoding: str = "cp866",
    force_reextract: bool = False,
) -> list[Path]:
    extract_zip_archives(
        input_dir,
        filename_encoding=filename_encoding,
        force_reextract=force_reextract,
    )
    pdfs = sorted(set(input_dir.rglob("*.pdf")))
    return [p for p in pdfs if p.is_file()]
